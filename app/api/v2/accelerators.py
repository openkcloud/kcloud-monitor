"""가속기(GPU/NPU)와 그 파티션 조회 라우터

벤더별 exporter 3종을 하나의 응답 모델로 통일:
- NVIDIA GPU: DCGM
- Furiosa NPU: kcloud_furiosa_* (2026-10 ETRI kcloud exporter. 이전 furiosa_npu_*)
- Rebellions NPU: RBLN_*

벤더는 cluster 경로 파라미터로 판별: l40s -> nvidia, furiosa* -> furiosa, rebellions -> rebellions
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request

from app.api.v2.deps import PaginationParams, TimeseriesParams
from app.schemas.accelerators import (
    AcceleratorDetailResponse,
    AcceleratorInfo,
    AcceleratorItem,
    AcceleratorListResponse,
    AcceleratorMetricsData,
    AcceleratorMetricsResponse,
    AcceleratorPowerResponse,
    AcceleratorPowerTimeseriesResponse,
    AcceleratorSummaryData,
    AcceleratorTemperatureResponse,
    AcceleratorTopologyData,
    AcceleratorTopologyResponse,
    PartitionDetailResponse,
    PartitionListResponse,
    PartitionPowerData,
    PartitionPowerResponse,
    PartitionPowerTimeseriesResponse,
    PowerData,
    PowerSeriesItem,
    TemperatureData,
    TopologyLinkItem,
)
from app.services.cluster_discovery import cluster_discovery
from app.services.power import KNOWN_TDP_WATTS
from app.services.prometheus import prometheus_client

router = APIRouter()

MIB_TO_BYTES = 1024 * 1024

# 전력 캡: 상한 대비 이 비율(%) 이상이면 캡에 걸린 것으로 본다. 실측 상한이 없으면 스펙 TDP로 대체.
POWER_CAP_NEAR_PERCENT = 95.0

# 쓰로틀링 간접 판정 초기값. 재현 시험(고정 최대 부하 + 냉각 제한)으로 맞춘다.
# 쓰로틀링은 클럭만 낮추므로 사용률은 100%로 유지될 수 있다. 사용률 급락 대신 전력 하락을 본다.
THROTTLE_UTIL_MIN = 90.0  # 2분 평균 사용률(%) 이상
THROTTLE_POWER_DROP = 0.85  # 현재 전력 < 15분 최대 전력 × 이 값

# DCGM 클럭 사유 비트 중 성능 제한에 해당하는 것만. GPU_IDLE(0x1)·앱 클럭 설정(0x2) 등은 제외.
CLOCK_REASON_BITS = {
    0x4: "sw_power_cap",
    0x8: "hw_slowdown",
    0x20: "sw_thermal",
    0x40: "hw_thermal",
    0x80: "hw_power_brake",
}
POWER_CAP_REASONS = {"sw_power_cap", "hw_power_brake"}

VENDOR_CONFIG: dict[str, dict] = {
    "nvidia": {
        "id_label": "UUID",
        "model_label": "modelName",
        "util": 'DCGM_FI_DEV_GPU_UTIL{{cluster="{cluster}"{extra}}}',
        "temp": 'DCGM_FI_DEV_GPU_TEMP{{cluster="{cluster}"{extra}}}',
        "power": 'DCGM_FI_DEV_POWER_USAGE{{cluster="{cluster}"{extra}}}',
        "mem_used": 'DCGM_FI_DEV_FB_USED{{cluster="{cluster}"{extra}}}',
        "mem_free": 'DCGM_FI_DEV_FB_FREE{{cluster="{cluster}"{extra}}}',
        "xid_errors": 'changes(DCGM_FI_DEV_XID_ERRORS{{cluster="{cluster}"{extra}}}[10m])',
        "sm_clock": 'DCGM_FI_DEV_SM_CLOCK{{cluster="{cluster}"{extra}}}',
        "mem_clock": 'DCGM_FI_DEV_MEM_CLOCK{{cluster="{cluster}"{extra}}}',
        "mem_copy_util": 'DCGM_FI_DEV_MEM_COPY_UTIL{{cluster="{cluster}"{extra}}}',
        "dec_util": 'DCGM_FI_DEV_DEC_UTIL{{cluster="{cluster}"{extra}}}',
        "enc_util": 'DCGM_FI_DEV_ENC_UTIL{{cluster="{cluster}"{extra}}}',
        "pcie_replay": 'DCGM_FI_DEV_PCIE_REPLAY_COUNTER{{cluster="{cluster}"{extra}}}',
        "nvlink_bw": 'DCGM_FI_DEV_NVLINK_BANDWIDTH_TOTAL{{cluster="{cluster}"{extra}}}',
        # 둘 다 기본 counters.csv에 없어 추가 전까지는 비어 있다. 비면 스펙 TDP·간접 판정으로 대체.
        "power_limit": 'DCGM_FI_DEV_POWER_MGMT_LIMIT{{cluster="{cluster}"{extra}}}',
        # DCGM 3.3부터 CLOCK_THROTTLE_REASONS가 CLOCKS_EVENT_REASONS로 이름이 바뀌어 둘 다 잡는다.
        "clock_reasons": '{{__name__=~"DCGM_FI_DEV_CLOCK(S_EVENT|_THROTTLE)_REASONS",cluster="{cluster}"{extra}}}',
        "throttle_temp": 85,  # 초기값
        "extra_keys": [
            "sm_clock", "mem_clock", "mem_copy_util", "dec_util", "enc_util", "pcie_replay",
        ],
    },
    # kcloud exporter 라벨: device(npu0), node/instance(워커명), pci, core(사용률만), sensor(온도만).
    # uuid·모델 라벨이 없어 device를 카드 ID로 쓰고 모델은 고정값. 클럭·쓰로틀 메트릭은 제공되지 않는다.
    # ponytail: device는 노드 안에서만 유일. 한 클러스터에 워커가 여럿이면 클러스터 단위 조회에서 겹친다.
    "furiosa": {
        "id_label": "device",
        "model_label": "model",
        "model_default": "RNGD",
        "util": 'avg(kcloud_furiosa_core_utilization{{cluster="{cluster}"{extra}}}) by (device,instance,node)',
        "temp": 'kcloud_furiosa_temperature_celsius{{sensor="soc_peak",cluster="{cluster}"{extra}}}',
        "power": 'kcloud_furiosa_power_watts{{cluster="{cluster}"{extra}}}',
        "mem_used": 'kcloud_furiosa_memory_used_bytes{{cluster="{cluster}"{extra}}}',
        "mem_total": 'kcloud_furiosa_memory_total_bytes{{cluster="{cluster}"{extra}}}',
        "alive": 'kcloud_furiosa_device_alive{{cluster="{cluster}"{extra}}}',
        "throttle_temp": 85,  # 초기값. RNGD 쓰로틀링 진입 온도 미공개
        "extra_keys": [],
    },
    "rebellions": {
        "id_label": "uuid",
        "model_label": "card",
        "util": 'RBLN_DEVICE_STATUS:UTILIZATION{{cluster="{cluster}"{extra}}}',
        # temp·power ×1000: exporter가 실측의 1/1000로 표출 (rbln-stat 대조 확정, 2026-08-24)
        "temp": '(RBLN_DEVICE_STATUS:TEMPERATURE{{cluster="{cluster}"{extra}}}) * 1000',
        "power": '(RBLN_DEVICE_STATUS:CARD_POWER{{cluster="{cluster}"{extra}}}) * 1000',
        "mem_used": 'RBLN_DEVICE_STATUS:DRAM_USED{{cluster="{cluster}"{extra}}}',
        "mem_total": 'RBLN_DEVICE_STATUS:DRAM_TOTAL{{cluster="{cluster}"{extra}}}',
        "health": 'RBLN_DEVICE_STATUS:HEALTH{{cluster="{cluster}"{extra}}}',
        # 드라이버 쓰로틀링 진입 온도. 2025-09-30 릴리스부터 90°C(이전 75°C). ×1000 보정 후 °C 기준
        "throttle_temp": 90,
        "extra_keys": [],
    },
}


def _esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


async def _get_vendor(cluster: str) -> Optional[str]:
    info = await cluster_discovery.get_cluster(cluster)
    return info.vendor if info else None


def _build_query(vendor: str, key: str, cluster: str, acc_id: Optional[str] = None) -> Optional[str]:
    template = VENDOR_CONFIG[vendor].get(key)
    if not template:
        return None
    extra = ""
    if acc_id is not None:
        extra = f',{VENDOR_CONFIG[vendor]["id_label"]}="{_esc(acc_id)}"'
    return template.format(cluster=_esc(cluster), extra=extra)


async def _instant_metric(vendor: str, cluster: str, key: str, acc_id: Optional[str] = None) -> list[dict]:
    query = _build_query(vendor, key, cluster, acc_id=acc_id)
    if query is None:
        return []
    return await prometheus_client.instant(query)


async def _range_metric(
    vendor: str, cluster: str, key: str, start: str, end: str, step: str, acc_id: Optional[str] = None
) -> list[dict]:
    query = _build_query(vendor, key, cluster, acc_id=acc_id)
    if query is None:
        return []
    return await prometheus_client.range_query(query, start, end, step)


def _throttle_query(vendor: str, cluster: str, acc_id: Optional[str] = None) -> str:
    """카드별 쓰로틀링 판정값(1 = 쓰로틀링, 0 = 아님). 사용률 유지, 최근 고온, 전력이 최근 최대보다 떨어짐을 곱한다.

    세 조건을 bool 비교 결과(0/1)로 곱하므로 원시값이 하나라도 비면 점 자체가 없다(판정 불가).
    값이 바뀌는 순간만 잡는 delta 대신 구간 집계를 써서 쓰로틀링이 이어지는 동안 계속 1이 된다.
    """
    util, temp, power = (_build_query(vendor, k, cluster, acc_id=acc_id) for k in ("util", "temp", "power"))
    on = f'on({VENDOR_CONFIG[vendor]["id_label"]}, instance)'
    return (
        f"(avg_over_time(({util})[2m:15s]) >= bool {THROTTLE_UTIL_MIN})"
        f" * {on} (max_over_time(({temp})[10m:15s]) >= bool {VENDOR_CONFIG[vendor]['throttle_temp']})"
        f" * {on} (({power}) < bool {THROTTLE_POWER_DROP} * max_over_time(({power})[15m:15s]))"
    )


async def throttle_series(
    vendor: str, cluster: str, start: str, end: str, step: str,
    node: Optional[str] = None, acc_id: Optional[str] = None,
) -> list[dict]:
    """기간 안의 카드별 쓰로틀링 판정 시계열. 반환: [{node, acc_id, values: [(시각, "0"|"1")]}]."""
    id_label = VENDOR_CONFIG[vendor]["id_label"]
    out = []
    for item in await prometheus_client.range_query(_throttle_query(vendor, cluster, acc_id=acc_id), start, end, step):
        metric = item.get("metric", {})
        if not _match_node(metric, node) or not metric.get(id_label):
            continue
        out.append({
            "node": metric.get("instance") or metric.get("hostname"),
            "acc_id": metric[id_label],
            "values": [
                (datetime.fromtimestamp(float(t), tz=timezone.utc).isoformat(), str(int(float(v))))
                for t, v in item.get("values", [])
            ],
        })
    return out


def count_throttle(values: list[tuple[str, str]], step_seconds: float) -> tuple[int, float]:
    """(쓰로틀링 시작 횟수, 쓰로틀링 시간 분). 1이 시작되는 지점을 센다. 직전 점이 0이거나 비어 있으면 새 시작."""
    events, ones, prev_t, prev_v = 0, 0, None, None
    for iso, v in values:
        t = datetime.fromisoformat(iso).timestamp()
        if v == "1":
            ones += 1
            if prev_v != "1" or t - prev_t > step_seconds:
                events += 1
        prev_t, prev_v = t, v
    return events, ones * step_seconds / 60


def _apply_power_cap_and_throttle(entry: dict, vendor: str) -> None:
    """수집 중간값(_power_limit, _clock_reasons, _inferred)으로 전력 캡 상태와 쓰로틀링 여부를 채운다."""
    raw_reasons = entry.pop("_clock_reasons", None)
    reasons = (
        None if raw_reasons is None
        else [name for bit, name in CLOCK_REASON_BITS.items() if int(raw_reasons) & bit]
    )

    measured = entry.pop("_power_limit", None)
    limit = measured or KNOWN_TDP_WATTS.get(vendor)
    power = entry.get("power_watts")
    percent = power / limit * 100 if power is not None and limit else None
    entry["power_limit_watts"] = limit
    entry["power_limit_source"] = ("measured" if measured else "spec_tdp") if limit else None
    entry["power_limit_percent"] = percent
    if reasons is not None:
        entry["power_capped"] = bool(POWER_CAP_REASONS & set(reasons))
    else:
        entry["power_capped"] = percent >= POWER_CAP_NEAR_PERCENT if percent is not None else None

    inferred = entry.pop("_inferred", None)  # 판정 쿼리 값 1/0. 없으면 원시값이 빠져 미판정
    if reasons is not None:
        entry["throttled"], entry["throttle_source"], entry["throttle_reasons"] = bool(reasons), "clock_reason", reasons
    elif inferred is not None:
        entry["throttled"], entry["throttle_source"] = inferred == 1.0, "inferred"


def _match_node(metric: dict, node: Optional[str]) -> bool:
    if node is None:
        return True
    # 호스트 라벨이 벤더마다 다름: NVIDIA Hostname, 리벨리온 hostname, 퓨리오사 node
    return node in (metric.get("instance"), metric.get("Hostname"), metric.get("hostname"), metric.get("node"))


def _get_value(item: dict) -> Optional[float]:
    try:
        return float(item["value"][1])
    except (KeyError, IndexError, ValueError, TypeError):
        return None


async def _collect_accelerators(
    cluster: str, vendor: str, node: Optional[str] = None
) -> tuple[dict[str, dict], list[str]]:
    """cluster/vendor 스코프의 가속기별 사용률·온도·전력·메모리·헬스 수집. node 지정 시 필터."""
    warnings: list[str] = []
    acc_map: dict[str, dict] = {}
    id_label = VENDOR_CONFIG[vendor]["id_label"]

    def merge(results: list[dict], field: str) -> bool:
        found = False
        for item in results:
            metric = item.get("metric", {})
            if not _match_node(metric, node):
                continue
            acc_id = metric.get(id_label)
            if not acc_id:
                continue
            found = True
            entry = acc_map.setdefault(acc_id, {"labels": {}})
            entry["labels"].update(metric)
            entry[field] = _get_value(item)
        return found

    if not merge(await _instant_metric(vendor, cluster, "util"), "utilization_percent"):
        warnings.append("NO_DATA_UTILIZATION")
    if not merge(await _instant_metric(vendor, cluster, "temp"), "temperature_celsius"):
        warnings.append("NO_DATA_TEMPERATURE")
    if not merge(await _instant_metric(vendor, cluster, "power"), "power_watts"):
        warnings.append("NO_DATA_POWER")

    if vendor == "nvidia":
        merge(await _instant_metric(vendor, cluster, "mem_used"), "_mem_used_mib")
        merge(await _instant_metric(vendor, cluster, "mem_free"), "_mem_free_mib")
        for entry in acc_map.values():
            used = entry.pop("_mem_used_mib", None)
            free = entry.pop("_mem_free_mib", None)
            entry["memory_used_bytes"] = used * MIB_TO_BYTES if used is not None else None
            entry["memory_total_bytes"] = (
                (used + free) * MIB_TO_BYTES if used is not None and free is not None else None
            )
        merge(await _instant_metric(vendor, cluster, "xid_errors"), "_xid")
        for entry in acc_map.values():
            xid = entry.pop("_xid", None)
            entry["healthy"] = (xid == 0.0) if xid is not None else None
    elif vendor == "furiosa":
        merge(await _instant_metric(vendor, cluster, "mem_used"), "memory_used_bytes")
        merge(await _instant_metric(vendor, cluster, "mem_total"), "memory_total_bytes")
        merge(await _instant_metric(vendor, cluster, "alive"), "_alive")
        for entry in acc_map.values():
            alive = entry.pop("_alive", None)
            entry["healthy"] = (alive == 1.0) if alive is not None else None
    elif vendor == "rebellions":
        merge(await _instant_metric(vendor, cluster, "mem_used"), "memory_used_bytes")
        merge(await _instant_metric(vendor, cluster, "mem_total"), "memory_total_bytes")
        merge(await _instant_metric(vendor, cluster, "health"), "_health")
        for entry in acc_map.values():
            health = entry.pop("_health", None)
            entry["healthy"] = (health == 0.0) if health is not None else None

    if vendor == "nvidia":
        merge(await _instant_metric(vendor, cluster, "power_limit"), "_power_limit")
        merge(await _instant_metric(vendor, cluster, "clock_reasons"), "_clock_reasons")
    merge(await prometheus_client.instant(_throttle_query(vendor, cluster)), "_inferred")
    for entry in acc_map.values():
        _apply_power_cap_and_throttle(entry, vendor)

    if not acc_map:
        warnings.append("NO_DATA")

    return acc_map, warnings


def _to_item(acc_id: str, entry: dict, vendor: str, cluster: str, node: Optional[str]) -> AcceleratorItem:
    labels = entry.get("labels", {})
    model_label = VENDOR_CONFIG[vendor]["model_label"]
    return AcceleratorItem(
        acc_id=acc_id,
        vendor=vendor,
        cluster=cluster,
        node=node or labels.get("instance") or labels.get("hostname"),
        model=labels.get(model_label) or VENDOR_CONFIG[vendor].get("model_default"),
        utilization_percent=entry.get("utilization_percent"),
        temperature_celsius=entry.get("temperature_celsius"),
        power_watts=entry.get("power_watts"),
        memory_used_bytes=entry.get("memory_used_bytes"),
        memory_total_bytes=entry.get("memory_total_bytes"),
        healthy=entry.get("healthy"),
        power_limit_watts=entry.get("power_limit_watts"),
        power_limit_source=entry.get("power_limit_source"),
        power_limit_percent=entry.get("power_limit_percent"),
        power_capped=entry.get("power_capped"),
        throttled=entry.get("throttled"),
        throttle_source=entry.get("throttle_source"),
        throttle_reasons=entry.get("throttle_reasons", []),
        labels=labels,
    )


def _to_info(acc_id: str, entry: dict, vendor: str, cluster: str, node: Optional[str]) -> AcceleratorInfo:
    """상세 응답용. 목록 항목에서 정체 정보만 남긴다."""
    item = _to_item(acc_id, entry, vendor, cluster, node)
    return AcceleratorInfo(**item.model_dump(include=set(AcceleratorInfo.model_fields)))


def _summarize(acc_map: dict[str, dict], vendor: str) -> AcceleratorSummaryData:
    """가속기 여러 장을 하나로 합친 집계값. 값이 없는 항목은 평균·합계에서 제외."""
    utils = [e["utilization_percent"] for e in acc_map.values() if e.get("utilization_percent") is not None]
    temps = [e["temperature_celsius"] for e in acc_map.values() if e.get("temperature_celsius") is not None]
    powers = [e["power_watts"] for e in acc_map.values() if e.get("power_watts") is not None]

    def avg(values: list[float]) -> Optional[float]:
        return sum(values) / len(values) if values else None

    return AcceleratorSummaryData(
        count=len(acc_map),
        vendor=vendor,
        avg_utilization_percent=avg(utils),
        avg_temperature_celsius=avg(temps),
        avg_power_watts=avg(powers),
        total_power_watts=sum(powers) if powers else None,
    )


async def _extra_metrics(vendor: str, cluster: str, node: Optional[str], acc_id: str) -> dict[str, float]:
    extras: dict[str, float] = {}
    for key in VENDOR_CONFIG[vendor].get("extra_keys", []):
        for item in await _instant_metric(vendor, cluster, key, acc_id=acc_id):
            if not _match_node(item.get("metric", {}), node):
                continue
            val = _get_value(item)
            if val is not None:
                extras[key] = val
                break
    return extras


async def _single_metric(
    vendor: str, cluster: str, node: Optional[str], acc_id: str, key: str
) -> tuple[Optional[float], list[str]]:
    """가속기 1장의 단일 메트릭만 조회. 전체 수집(_collect_accelerators) 없이 1쿼리로 끝낸다."""
    for item in await _instant_metric(vendor, cluster, key, acc_id=acc_id):
        if not _match_node(item.get("metric", {}), node):
            continue
        value = _get_value(item)
        return value, [] if value is not None else ["NO_DATA"]
    return None, ["ACCELERATOR_NOT_FOUND"]


# ---------------------------------------------------------------------------
# Accelerators (노드 하위 canonical 경로)
# ---------------------------------------------------------------------------


@router.get("/clusters/{cluster}/nodes/{node}/accelerators", summary="가속기 목록")
async def list_accelerators(
    request: Request, cluster: str, node: str, params: PaginationParams = Depends()
) -> AcceleratorListResponse:
    """특정 노드에 장착된 가속기(GPU/NPU) 목록 조회

    - 가속기 ID(UUID), 벤더, 모델명
    - 사용률(%), 온도(°C), 전력(W)
    - 사용 메모리, 총 메모리(bytes)
    - 정상 동작 여부
    - 전력 캡 상태: 전력 상한(W)과 출처(카드 설정값 또는 스펙 TDP), 상한 대비 전력(%), 상한 도달 여부
    - 쓰로틀링 여부와 판정 근거(클럭 제한 사유 직접 관측 또는 사용률·온도·전력 간접 판정)
    - total, summary : 전체 개수와 집계 (평균 사용률·온도·전력, 전력 합계)
    """
    vendor = await _get_vendor(cluster)
    if vendor is None:
        return AcceleratorListResponse(status="partial", data=[], warnings=["UNKNOWN_CLUSTER"])

    acc_map, warnings = await _collect_accelerators(cluster, vendor, node=node)
    items = [_to_item(acc_id, entry, vendor, cluster, node) for acc_id, entry in acc_map.items()]

    # 집계는 페이지와 무관하게 전체 카드 기준 — slicing 전에 계산한다.
    total = len(acc_map)
    summary = _summarize(acc_map, vendor)

    offset = params.offset
    limit = params.limit
    items = items[offset : offset + limit]

    return AcceleratorListResponse(
        status="success" if items else "partial",
        data=items,
        total=total,
        summary=summary,
        warnings=warnings,
    )


@router.get("/clusters/{cluster}/nodes/{node}/accelerators/topology", summary="가속기 인터커넥트 토폴로지")
async def get_accelerators_topology(
    request: Request, cluster: str, node: str
) -> AcceleratorTopologyResponse:
    """가속기끼리 직접 연결된 통신 링크(NVLink) 조회

    - 벤더
    - 링크별 대역폭 값과 어느 카드에서 어느 카드로 이어지는지 나타내는 라벨
    - NVIDIA 클러스터만 값이 채워지고 NPU 클러스터는 빈 목록 반환
    """
    vendor = await _get_vendor(cluster)
    if vendor != "nvidia":
        return AcceleratorTopologyResponse(
            status="partial",
            data=AcceleratorTopologyData(vendor=vendor, links=[]),
            warnings=["TOPOLOGY_NOT_AVAILABLE"],
        )

    results = await _instant_metric(vendor, cluster, "nvlink_bw")
    links = [
        TopologyLinkItem(metric_labels=item.get("metric", {}), value=_get_value(item))
        for item in results
        if _match_node(item.get("metric", {}), node)
    ]

    return AcceleratorTopologyResponse(
        status="success" if links else "partial",
        data=AcceleratorTopologyData(vendor=vendor, links=links),
        warnings=[] if links else ["NO_DATA"],
    )


@router.get("/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}", summary="가속기 상세")
async def get_accelerator(
    request: Request, cluster: str, node: str, acc_id: str
) -> AcceleratorDetailResponse:
    """가속기 ID(UUID)로 단일 가속기 상세 조회

    - 가속기 ID, 벤더, 클러스터, 노드, 모델명
    - 총 메모리(bytes)
    - 전력 상한(W)과 출처(카드 설정값 또는 스펙 TDP)
    - 사용률, 온도, 전력 같은 계속 바뀌는 값은 .../metrics 에서 조회
    """
    vendor = await _get_vendor(cluster)
    if vendor is None:
        return AcceleratorDetailResponse(status="partial", data=None, warnings=["UNKNOWN_CLUSTER"])

    acc_map, warnings = await _collect_accelerators(cluster, vendor, node=node)
    entry = acc_map.get(acc_id)
    if entry is None:
        warnings.append("ACCELERATOR_NOT_FOUND")
        return AcceleratorDetailResponse(status="partial", data=None, warnings=warnings)

    return AcceleratorDetailResponse(
        status="success", data=_to_info(acc_id, entry, vendor, cluster, node), warnings=warnings
    )


@router.get("/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/metrics", summary="가속기 실시간 메트릭")
async def get_accelerator_metrics(
    request: Request, cluster: str, node: str, acc_id: str
) -> AcceleratorMetricsResponse:
    """가속기 한 장이 지금 얼마나 일하고 있는지 조회

    - 사용률(%), 사용 메모리, 총 메모리(bytes)
    - 전력(W), 온도(°C), 정상 동작 여부
    - 전력 캡 상태: 상한 대비 전력(%), 상한 도달 여부
    - 쓰로틀링 여부와 판정 근거(클럭 제한 사유 직접 관측 또는 사용률, 온도, 전력 간접 판정)
    - 벤더별 부가 메트릭(현재 NVIDIA만): 코어/메모리 클럭(MHz), 인코딩, 디코딩 사용률(%), PCIe 재전송 누적 횟수
    """
    vendor = await _get_vendor(cluster)
    if vendor is None:
        return AcceleratorMetricsResponse(
            status="partial", acc_id=acc_id, data=AcceleratorMetricsData(), warnings=["UNKNOWN_CLUSTER"]
        )

    acc_map, warnings = await _collect_accelerators(cluster, vendor, node=node)
    entry = acc_map.get(acc_id)
    if entry is None:
        warnings.append("ACCELERATOR_NOT_FOUND")
        return AcceleratorMetricsResponse(
            status="partial", acc_id=acc_id, vendor=vendor, data=AcceleratorMetricsData(), warnings=warnings
        )

    extras = await _extra_metrics(vendor, cluster, node, acc_id)
    data = AcceleratorMetricsData(
        utilization_percent=entry.get("utilization_percent"),
        memory_used_bytes=entry.get("memory_used_bytes"),
        memory_total_bytes=entry.get("memory_total_bytes"),
        power_watts=entry.get("power_watts"),
        temperature_celsius=entry.get("temperature_celsius"),
        healthy=entry.get("healthy"),
        power_limit_percent=entry.get("power_limit_percent"),
        power_capped=entry.get("power_capped"),
        throttled=entry.get("throttled"),
        throttle_source=entry.get("throttle_source"),
        throttle_reasons=entry.get("throttle_reasons", []),
        extra=extras,
    )

    return AcceleratorMetricsResponse(status="success", acc_id=acc_id, vendor=vendor, data=data, warnings=warnings)


@router.get("/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/power", summary="가속기 전력")
async def get_accelerator_power(
    request: Request, cluster: str, node: str, acc_id: str
) -> AcceleratorPowerResponse:
    """가속기 한 장이 지금 쓰고 있는 전력 조회

    - 가속기 ID
    - 전력(W), 벤더 exporter가 직접 보고한 실측값
    """
    vendor = await _get_vendor(cluster)
    if vendor is None:
        return AcceleratorPowerResponse(
            status="partial", acc_id=acc_id, data=PowerData(), warnings=["UNKNOWN_CLUSTER"]
        )

    watts, warnings = await _single_metric(vendor, cluster, node, acc_id, "power")
    if watts is None:
        return AcceleratorPowerResponse(status="partial", acc_id=acc_id, data=PowerData(), warnings=warnings)

    return AcceleratorPowerResponse(
        status="success", acc_id=acc_id, data=PowerData(power_watts=watts), warnings=warnings
    )


@router.get(
    "/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/power/timeseries",
    summary="가속기 전력 시계열",
)
async def get_accelerator_power_timeseries(
    request: Request, cluster: str, node: str, acc_id: str, params: TimeseriesParams = Depends()
) -> AcceleratorPowerTimeseriesResponse:
    """가속기 한 장의 전력 변화 추이 조회

    - 가속기 ID
    - (시각, 전력값) 쌍 목록
    - 조회 기간과 간격은 period, start, end, step 파라미터로 지정
    """
    vendor = await _get_vendor(cluster)
    if vendor is None:
        return AcceleratorPowerTimeseriesResponse(
            status="partial", acc_id=acc_id, series=[], warnings=["UNKNOWN_CLUSTER"]
        )

    now = datetime.now(timezone.utc)
    start = params.start or (now - timedelta(hours=1)).isoformat()
    end = params.end or now.isoformat()
    step = params.step

    results = await _range_metric(vendor, cluster, "power", start, end, step, acc_id=acc_id)
    filtered = [item for item in results if _match_node(item.get("metric", {}), node)]

    series = [
        PowerSeriesItem(
            metric_labels=item.get("metric", {}),
            values=[
                (datetime.fromtimestamp(float(v[0]), tz=timezone.utc).isoformat(), str(v[1]))
                for v in item.get("values", [])
            ],
        )
        for item in filtered
    ]

    return AcceleratorPowerTimeseriesResponse(
        status="success" if series else "partial",
        acc_id=acc_id,
        series=series,
        warnings=[] if series else ["NO_DATA"],
    )


@router.get("/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/temperature", summary="가속기 온도")
async def get_accelerator_temperature(
    request: Request, cluster: str, node: str, acc_id: str
) -> AcceleratorTemperatureResponse:
    """가속기 한 장의 현재 온도 조회

    - 가속기 ID
    - 온도(°C)
    """
    vendor = await _get_vendor(cluster)
    if vendor is None:
        return AcceleratorTemperatureResponse(
            status="partial", acc_id=acc_id, data=TemperatureData(), warnings=["UNKNOWN_CLUSTER"]
        )

    celsius, warnings = await _single_metric(vendor, cluster, node, acc_id, "temp")
    if celsius is None:
        return AcceleratorTemperatureResponse(
            status="partial", acc_id=acc_id, data=TemperatureData(), warnings=warnings
        )

    return AcceleratorTemperatureResponse(
        status="success",
        acc_id=acc_id,
        data=TemperatureData(temperature_celsius=celsius),
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Partitions (MIG/파티셔닝 미사용 환경 — 파티션 메트릭 부재로 stub)
# ---------------------------------------------------------------------------


@router.get("/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/partitions", summary="파티션 목록")
async def list_partitions(
    request: Request, cluster: str, node: str, acc_id: str, params: PaginationParams = Depends()
) -> PartitionListResponse:
    """가속기 한 장을 여러 조각으로 나눈 파티션 목록 조회

    - 파티션 ID, 프로파일, 사용률(%)
    - 현재 카드 분할 기능을 쓰지 않아 status="not_implemented" 반환
    """
    return PartitionListResponse(status="partial", data=[], warnings=["PARTITION_DATA_NOT_AVAILABLE"])


@router.get(
    "/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/partitions/{partition_id}",
    summary="파티션 상세",
)
async def get_partition(
    request: Request, cluster: str, node: str, acc_id: str, partition_id: str
) -> PartitionDetailResponse:
    """파티션 ID로 단일 파티션 상세 조회

    - 파티션 ID, 프로파일, 사용률(%)
    - 현재 카드 분할 기능을 쓰지 않아 status="not_implemented" 반환
    """
    return PartitionDetailResponse(status="partial", data=None, warnings=["PARTITION_DATA_NOT_AVAILABLE"])


@router.get(
    "/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/partitions/{partition_id}/power",
    summary="파티션 전력",
)
async def get_partition_power(
    request: Request, cluster: str, node: str, acc_id: str, partition_id: str
) -> PartitionPowerResponse:
    """파티션 한 조각에 배분되는 전력 조회

    - 가속기 ID, 파티션 ID
    - 전력(W), 카드 전력을 파티션 점유 비율로 나눈 추정치
    - 현재 카드 분할 기능을 쓰지 않아 status="not_implemented" 반환
    """
    return PartitionPowerResponse(
        status="partial",
        acc_id=acc_id,
        partition_id=partition_id,
        data=PartitionPowerData(),
        warnings=["PARTITION_DATA_NOT_AVAILABLE"],
    )


@router.get(
    "/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/partitions/{partition_id}/power/timeseries",
    summary="파티션 전력 시계열",
)
async def get_partition_power_timeseries(
    request: Request,
    cluster: str,
    node: str,
    acc_id: str,
    partition_id: str,
    params: TimeseriesParams = Depends(),
) -> PartitionPowerTimeseriesResponse:
    """파티션 한 조각의 전력 변화 추이 조회

    - 가속기 ID, 파티션 ID
    - (시각, 전력값) 쌍 목록
    - 현재 카드 분할 기능을 쓰지 않아 status="not_implemented" 반환
    """
    return PartitionPowerTimeseriesResponse(
        status="partial",
        acc_id=acc_id,
        partition_id=partition_id,
        series=[],
        warnings=["PARTITION_DATA_NOT_AVAILABLE"],
    )


# ---------------------------------------------------------------------------
# 별칭(단축 경로) — 노드 없이 UUID로 직접 조회
# ---------------------------------------------------------------------------


def _accelerator_links(cluster: str, acc_id: str, node: Optional[str]) -> dict:
    """별칭 응답용 _links — self(단축 경로) + canonical(노드 포함 정규 경로)."""
    links = {"self": f"/api/v2/clusters/{cluster}/accelerators/{acc_id}"}
    if node:
        links["canonical"] = f"/api/v2/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}"
    return links


@router.get("/clusters/{cluster}/accelerators/{acc_id}", summary="가속기 상세(노드 생략 경로)")
async def get_accelerator_alias(request: Request, cluster: str, acc_id: str):
    """노드 이름을 몰라도 가속기 ID(UUID)만으로 상세 조회

    - 가속기 ID, 벤더, 클러스터, 노드, 모델명
    - 총 메모리(bytes)
    - 전력 상한(W)과 출처(카드 설정값 또는 스펙 TDP)
    - 사용률, 온도, 전력 같은 계속 바뀌는 값은 .../metrics 에서 조회
    - `_links.canonical` 에 노드까지 포함한 정식 경로 안내

    클러스터 전체를 훑어 ID가 일치하는 카드를 찾는 방식.
    """
    vendor = await _get_vendor(cluster)
    if vendor is None:
        resp = AcceleratorDetailResponse(status="partial", data=None, warnings=["UNKNOWN_CLUSTER"])
        return {**resp.model_dump(), "_links": _accelerator_links(cluster, acc_id, None)}

    acc_map, warnings = await _collect_accelerators(cluster, vendor, node=None)
    entry = acc_map.get(acc_id)
    if entry is None:
        warnings.append("ACCELERATOR_NOT_FOUND")
        resp = AcceleratorDetailResponse(status="partial", data=None, warnings=warnings)
        return {**resp.model_dump(), "_links": _accelerator_links(cluster, acc_id, None)}

    item = _to_info(acc_id, entry, vendor, cluster, None)
    resp = AcceleratorDetailResponse(status="success", data=item, warnings=warnings)
    return {**resp.model_dump(), "_links": _accelerator_links(cluster, acc_id, item.node)}


@router.get(
    "/clusters/{cluster}/accelerators/{acc_id}/partitions/{partition_id}",
    summary="파티션 상세(노드 생략 경로)",
)
async def get_partition_alias(
    request: Request, cluster: str, acc_id: str, partition_id: str
) -> PartitionDetailResponse:
    """노드 이름을 몰라도 파티션 ID만으로 상세 조회

    - 파티션 ID, 프로파일, 사용률(%)
    - 현재 카드 분할 기능을 쓰지 않아 status="not_implemented" 반환
    """
    return PartitionDetailResponse(status="partial", data=None, warnings=["PARTITION_DATA_NOT_AVAILABLE"])
