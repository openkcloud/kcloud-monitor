"""
KCloud Monitor v2 — Accelerators (8개) + Partitions (4개) + 별칭 2개.

가속기 = GPU(NVIDIA, DCGM)·NPU(Furiosa, furiosa_npu_*)·NPU(Rebellions, RBLN_*) 통합 모델.
벤더는 cluster 경로 파라미터로 판별한다: l40s→nvidia, k8s-furiosa-rngd→furiosa, rebellions→rebellions.
파티션(MIG/vGPU/NPU slice)은 MIG/파티셔닝 미사용 환경이라 파티션 메트릭이 부재하므로 stub 유지(빈 응답 + 경고).
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request

from app.api.v2.deps import PaginationParams, TimeseriesParams
from app.schemas.accelerators import (
    AcceleratorDetailResponse,
    AcceleratorItem,
    AcceleratorListResponse,
    AcceleratorMetricsData,
    AcceleratorMetricsResponse,
    AcceleratorPowerResponse,
    AcceleratorPowerTimeseriesResponse,
    AcceleratorSummaryData,
    AcceleratorSummaryResponse,
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
from app.services.prometheus import prometheus_client

router = APIRouter()

MIB_TO_BYTES = 1024 * 1024

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
        "extra_keys": [
            "sm_clock", "mem_clock", "mem_copy_util", "dec_util", "enc_util", "pcie_replay",
        ],
    },
    "furiosa": {
        "id_label": "uuid",
        "model_label": "arch",
        "util": 'avg(furiosa_npu_core_utilization{{cluster="{cluster}"{extra}}}) by (device,uuid,instance,hostname)',
        "temp": 'furiosa_npu_hw_temperature{{label="peak",cluster="{cluster}"{extra}}}',
        "power": 'furiosa_npu_hw_power{{cluster="{cluster}"{extra}}}',
        "mem_used": 'furiosa_npu_dram_usage{{cluster="{cluster}"{extra}}}',
        "mem_total": 'furiosa_npu_dram_total{{cluster="{cluster}"{extra}}}',
        "alive": 'furiosa_npu_alive{{cluster="{cluster}"{extra}}}',
        "freq": 'furiosa_npu_core_frequency{{cluster="{cluster}"{extra}}}',
        "throttle": 'furiosa_npu_throttling_events_count{{cluster="{cluster}"{extra}}}',
        "extra_keys": ["freq", "throttle"],
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


def _match_node(metric: dict, node: Optional[str]) -> bool:
    if node is None:
        return True
    return metric.get("instance") == node or metric.get("hostname") == node


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
        model=labels.get(model_label),
        utilization_percent=entry.get("utilization_percent"),
        temperature_celsius=entry.get("temperature_celsius"),
        power_watts=entry.get("power_watts"),
        memory_used_bytes=entry.get("memory_used_bytes"),
        memory_total_bytes=entry.get("memory_total_bytes"),
        healthy=entry.get("healthy"),
        labels=labels,
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


# ---------------------------------------------------------------------------
# Accelerators (노드 하위 canonical 경로)
# ---------------------------------------------------------------------------


@router.get("/clusters/{cluster}/nodes/{node}/accelerators", summary="가속기 목록")
async def list_accelerators(
    request: Request, cluster: str, node: str, params: PaginationParams = Depends()
) -> AcceleratorListResponse:
    """노드의 가속기 목록 — UUID·모델·사용률·온도·전력·헬스."""
    vendor = await _get_vendor(cluster)
    if vendor is None:
        return AcceleratorListResponse(status="partial", data=[], warnings=["UNKNOWN_CLUSTER"])

    acc_map, warnings = await _collect_accelerators(cluster, vendor, node=node)
    items = [_to_item(acc_id, entry, vendor, cluster, node) for acc_id, entry in acc_map.items()]

    offset = params.offset
    limit = params.limit
    items = items[offset : offset + limit]

    return AcceleratorListResponse(
        status="success" if items else "partial", data=items, warnings=warnings
    )


@router.get("/clusters/{cluster}/nodes/{node}/accelerators/summary", summary="가속기 집계 요약")
async def get_accelerators_summary(
    request: Request, cluster: str, node: str
) -> AcceleratorSummaryResponse:
    """노드 가속기 집계 — 수량, 평균 사용률/온도/전력."""
    vendor = await _get_vendor(cluster)
    if vendor is None:
        return AcceleratorSummaryResponse(
            status="partial",
            data=AcceleratorSummaryData(count=0),
            warnings=["UNKNOWN_CLUSTER"],
        )

    acc_map, warnings = await _collect_accelerators(cluster, vendor, node=node)
    utils = [e["utilization_percent"] for e in acc_map.values() if e.get("utilization_percent") is not None]
    temps = [e["temperature_celsius"] for e in acc_map.values() if e.get("temperature_celsius") is not None]
    powers = [e["power_watts"] for e in acc_map.values() if e.get("power_watts") is not None]

    def avg(values: list[float]) -> Optional[float]:
        return sum(values) / len(values) if values else None

    data = AcceleratorSummaryData(
        count=len(acc_map),
        vendor=vendor,
        avg_utilization_percent=avg(utils),
        avg_temperature_celsius=avg(temps),
        avg_power_watts=avg(powers),
        total_power_watts=sum(powers) if powers else None,
    )

    return AcceleratorSummaryResponse(status="success" if acc_map else "partial", data=data, warnings=warnings)


@router.get("/clusters/{cluster}/nodes/{node}/accelerators/topology", summary="가속기 인터커넥트 토폴로지")
async def get_accelerators_topology(
    request: Request, cluster: str, node: str
) -> AcceleratorTopologyResponse:
    """가속기 인터커넥트 — NVLink 대역폭(L40S만 제공)."""
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
    """단일 가속기 상세 — UUID 매칭."""
    vendor = await _get_vendor(cluster)
    if vendor is None:
        return AcceleratorDetailResponse(status="partial", data=None, warnings=["UNKNOWN_CLUSTER"])

    acc_map, warnings = await _collect_accelerators(cluster, vendor, node=node)
    entry = acc_map.get(acc_id)
    if entry is None:
        warnings.append("ACCELERATOR_NOT_FOUND")
        return AcceleratorDetailResponse(status="partial", data=None, warnings=warnings)

    return AcceleratorDetailResponse(
        status="success", data=_to_item(acc_id, entry, vendor, cluster, node), warnings=warnings
    )


@router.get("/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/metrics", summary="가속기 실시간 메트릭 [M2]")
async def get_accelerator_metrics(
    request: Request, cluster: str, node: str, acc_id: str
) -> AcceleratorMetricsResponse:
    """가속기 실시간 메트릭 — 사용률/메모리/클럭/쓰로틀/오류."""
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
        extra=extras,
    )

    return AcceleratorMetricsResponse(status="success", acc_id=acc_id, vendor=vendor, data=data, warnings=warnings)


@router.get("/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/power", summary="가속기 전력 실측 [P4]")
async def get_accelerator_power(
    request: Request, cluster: str, node: str, acc_id: str
) -> AcceleratorPowerResponse:
    """가속기 전력 — DCGM/furiosa_npu_hw_power/RBLN 실측. 전력 계층 P4."""
    vendor = await _get_vendor(cluster)
    if vendor is None:
        return AcceleratorPowerResponse(
            status="partial", acc_id=acc_id, data=PowerData(), warnings=["UNKNOWN_CLUSTER"]
        )

    acc_map, warnings = await _collect_accelerators(cluster, vendor, node=node)
    entry = acc_map.get(acc_id)
    if entry is None:
        warnings.append("ACCELERATOR_NOT_FOUND")
        return AcceleratorPowerResponse(status="partial", acc_id=acc_id, data=PowerData(), warnings=warnings)

    return AcceleratorPowerResponse(
        status="success", acc_id=acc_id, data=PowerData(power_watts=entry.get("power_watts")), warnings=warnings
    )


@router.get(
    "/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/power/timeseries",
    summary="가속기 전력 시계열",
)
async def get_accelerator_power_timeseries(
    request: Request, cluster: str, node: str, acc_id: str, params: TimeseriesParams = Depends()
) -> AcceleratorPowerTimeseriesResponse:
    """가속기 전력 시계열."""
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
    """가속기 온도."""
    vendor = await _get_vendor(cluster)
    if vendor is None:
        return AcceleratorTemperatureResponse(
            status="partial", acc_id=acc_id, data=TemperatureData(), warnings=["UNKNOWN_CLUSTER"]
        )

    acc_map, warnings = await _collect_accelerators(cluster, vendor, node=node)
    entry = acc_map.get(acc_id)
    if entry is None:
        warnings.append("ACCELERATOR_NOT_FOUND")
        return AcceleratorTemperatureResponse(
            status="partial", acc_id=acc_id, data=TemperatureData(), warnings=warnings
        )

    return AcceleratorTemperatureResponse(
        status="success",
        acc_id=acc_id,
        data=TemperatureData(temperature_celsius=entry.get("temperature_celsius")),
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Partitions (MIG/파티셔닝 미사용 환경 — 파티션 메트릭 부재로 stub)
# ---------------------------------------------------------------------------


@router.get("/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/partitions", summary="파티션 목록")
async def list_partitions(
    request: Request, cluster: str, node: str, acc_id: str, params: PaginationParams = Depends()
) -> PartitionListResponse:
    """가속기의 파티션 목록 — MIG/파티셔닝 미사용 환경. 파티션 메트릭 부재로 stub."""
    return PartitionListResponse(status="partial", data=[], warnings=["PARTITION_DATA_NOT_AVAILABLE"])


@router.get(
    "/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/partitions/{partition_id}",
    summary="파티션 상세",
)
async def get_partition(
    request: Request, cluster: str, node: str, acc_id: str, partition_id: str
) -> PartitionDetailResponse:
    """단일 파티션 상세 — MIG/파티셔닝 미사용 환경. 파티션 메트릭 부재로 stub."""
    return PartitionDetailResponse(status="partial", data=None, warnings=["PARTITION_DATA_NOT_AVAILABLE"])


@router.get(
    "/clusters/{cluster}/nodes/{node}/accelerators/{acc_id}/partitions/{partition_id}/power",
    summary="파티션 전력 추정 [P5]",
)
async def get_partition_power(
    request: Request, cluster: str, node: str, acc_id: str, partition_id: str
) -> PartitionPowerResponse:
    """파티션 전력 추정 [P5] — MIG/파티셔닝 미사용 환경. 파티션 메트릭 부재로 stub."""
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
    """파티션 전력 추정 시계열 — MIG/파티셔닝 미사용 환경. 파티션 메트릭 부재로 stub."""
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


@router.get("/clusters/{cluster}/accelerators/{acc_id}", summary="가속기 상세(단축 경로)")
async def get_accelerator_alias(request: Request, cluster: str, acc_id: str):
    """노드를 몰라도 UUID로 바로 조회하는 별칭 — 클러스터 전체에서 UUID 검색.

    canonical(정규) 경로는 노드를 포함한 /clusters/{cluster}/nodes/{node}/accelerators/{acc_id}이며,
    _links.canonical로 안내한다(노드를 특정할 수 없으면 생략).
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

    item = _to_item(acc_id, entry, vendor, cluster, None)
    resp = AcceleratorDetailResponse(status="success", data=item, warnings=warnings)
    return {**resp.model_dump(), "_links": _accelerator_links(cluster, acc_id, item.node)}


@router.get(
    "/clusters/{cluster}/accelerators/{acc_id}/partitions/{partition_id}",
    summary="파티션 상세(단축 경로)",
)
async def get_partition_alias(
    request: Request, cluster: str, acc_id: str, partition_id: str
) -> PartitionDetailResponse:
    """파티션 UUID 직접 조회 별칭 — MIG/파티셔닝 미사용 환경. 파티션 메트릭 부재로 stub."""
    return PartitionDetailResponse(status="partial", data=None, warnings=["PARTITION_DATA_NOT_AVAILABLE"])
