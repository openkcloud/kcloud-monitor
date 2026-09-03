"""노드(서버 한 대) 조회 라우터

운영체제 수준 지표는 node_exporter, 전력과 하드웨어 센서는 IPMI 기반.
노드 전력은 BMC 실측(벽면 전력) 하나로 통일했다. 노드 8대가 같은 기준이라 서로 비교할 수 있다.
CPU/메모리만 재는 Kepler는 측정 범위가 CPU 세대에 따라 갈려(psys/package) 노드 간 비교가
불가능하므로 노드 경로에서 쓰지 않고, 클러스터·시스템 요약의 CPU 계층에서만 제공한다.
hardware 하위 경로는 IPMI 센서를 걷어오지 않는 노드에서 IPMI_NOT_AVAILABLE 경고와
함께 빈 데이터 반환.
"""
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.v2.deps import PaginationParams, TimeseriesParams
from app.schemas.nodes import (
    CpuCoreUsage,
    CpuModeBreakdown,
    DiskIoRate,
    FilesystemUsage,
    HardwarePowerResponse,
    HardwareSensor,
    HardwareSensorsResponse,
    HardwareTemperatureResponse,
    HardwareTemperatureSensor,
    NetworkInterfaceStats,
    NodeCpuData,
    NodeCpuResponse,
    NodeDetailData,
    NodeDetailResponse,
    NodeListResponse,
    NodeMemoryData,
    NodeMemoryResponse,
    NodeMetricsData,
    NodeMetricsResponse,
    NodeNetworkData,
    NodeNetworkResponse,
    NodeOsInfo,
    NodePowerData,
    NodePowerResponse,
    NodePowerTimeseriesPoint,
    NodePowerTimeseriesResponse,
    NodeStorageData,
    NodeStorageResponse,
    NodeSummaryItem,
    NodesSummaryData,
    NodesSummaryResponse,
)
from app.services.cluster_discovery import ClusterInfo, cluster_discovery, cluster_label
from app.services.prometheus import prometheus_client

router = APIRouter()

# 실디스크 파일시스템만 남기는 PromQL 라벨 조건. tmpfs·ramfs·overlay 같은 가상 파일시스템을
# 용량 집계에 넣으면 사용률이 왜곡되고, `/run/user/*`처럼 로그인 수에 따라 개수가 변한다.
REAL_FS = 'fstype=~"ext4|xfs|btrfs"'


def _esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _cl(cluster: str) -> str:
    """cluster path param → escaped PromQL label value."""
    return _esc(cluster_label(cluster))


async def _require_cluster(cluster: str) -> ClusterInfo:
    """알 수 없는 클러스터는 404. clusters.py의 동일 계약을 nodes.py에도 적용."""
    info = await cluster_discovery.get_cluster(cluster)
    if info is None:
        raise HTTPException(status_code=404, detail=f"알 수 없는 클러스터: {cluster}")
    return info


async def _phys_node_set() -> set[str]:
    """machine_cpu_cores의 node 라벨 집합 — 물리 노드 판별 기준(clusters.py topology와 동일 로직)."""
    results = await prometheus_client.instant("machine_cpu_cores")
    return {n for it in results if (n := it.get("metric", {}).get("node"))}


def _card_id(metric: dict) -> str:
    """가속기 카드 식별자 (벤더 공통). clusters.py의 동일 함수 복사(순환 import 방지)."""
    return (
        metric.get("UUID") or metric.get("uuid") or metric.get("gpu")
        or metric.get("device") or "unknown"
    )


def _card_host(metric: dict) -> str:
    """카드가 보고된 호스트 (벤더별 라벨 차이 흡수). clusters.py의 동일 함수 복사."""
    return metric.get("Hostname") or metric.get("hostname") or metric.get("instance") or "unknown"


async def _accelerator_count_for_host(host: str) -> int:
    """해당 호스트(node)에 연결된 가속기(카드) 수. cluster 라벨 없이 hostname/Hostname으로
    매칭한다 — compute6처럼 cluster 라벨이 누락된 물리 L40S도 이 방식이면 잡힌다."""
    h = _esc(host)
    queries = [
        f'DCGM_FI_DEV_GPU_UTIL{{Hostname="{h}"}}',
        f'furiosa_npu_core_utilization{{hostname="{h}"}}',
        f'{{__name__="RBLN_DEVICE_STATUS:UTILIZATION",hostname="{h}"}}',
    ]
    ids: set[str] = set()
    for q in queries:
        for item in await prometheus_client.instant(q):
            ids.add(_card_id(item.get("metric", {})))
    return len(ids)


async def _service_cluster_hosts(
    info: ClusterInfo, phys_nodes: set[str]
) -> list[tuple[str, bool, int, Optional[float]]]:
    """서비스 클러스터(가속기 VM/K8s)의 노드(호스트) 목록 — 가속기 메트릭 hostname 기반.

    kube_node_info가 없는 l40s/rebellions/k8s-furiosa-rngd 등에서 노드 목록의 유일한 단서다.
    Returns: (host, is_physical, accelerator_count, power_watts) 목록.
    """
    if not info.utilization_query:
        return []
    util_res = await prometheus_client.instant(info.utilization_query)
    power_res = await prometheus_client.instant(info.power_query) if info.power_query else []

    power_by_id: dict[str, float] = {}
    for it in power_res:
        cid = _card_id(it.get("metric", {}))
        try:
            power_by_id[cid] = power_by_id.get(cid, 0.0) + float(it["value"][1])
        except (KeyError, IndexError, ValueError):
            continue

    host_cards: dict[str, set[str]] = {}
    for it in util_res:
        m = it.get("metric", {})
        host_cards.setdefault(_card_host(m), set()).add(_card_id(m))

    rows: list[tuple[str, bool, int, Optional[float]]] = []
    for host, cards in host_cards.items():
        powers = [power_by_id[c] for c in cards if c in power_by_id]
        rows.append((host, host in phys_nodes, len(cards), sum(powers) if powers else None))
    return rows


async def _resolve_instance(cluster: str, node: str) -> str:
    """노드 param이 k8s 노드 이름이면 node-exporter instance(IP:9100)로 변환.
    이미 instance 형태거나 해석 불가면 원본을 그대로 반환(하위호환)."""
    c = _cl(cluster)
    r = await prometheus_client.instant(
        f'node_uname_info{{cluster="{c}",nodename="{_esc(node)}"}}'
    )
    if r:
        inst = r[0].get("metric", {}).get("instance")
        if inst:
            return inst
    return node


def _first_value(results: list[dict]) -> Optional[float]:
    if not results:
        return None
    try:
        value = float(results[0]["value"][1])
    except (KeyError, IndexError, ValueError, TypeError):
        return None
    # IPMI 미적용 센서 등은 NaN/Inf로 오는데 JSON 비호환 → 값 없음으로 취급
    if math.isnan(value) or math.isinf(value):
        return None
    return value


async def _ipmi_node_power_instant(node: str) -> tuple[list[dict], str]:
    """물리 노드 서버 총전력(현재값) — BMC 실측. 결과와 산출 경로를 함께 반환.

    노드 전력은 IPMI를 기준으로 삼는다. BMC가 전원공급장치를 직접 읽으므로 CPU·메모리뿐
    아니라 가속기·팬·디스크·PSU 손실까지 포함한 벽면 전력이고, 노드 8대가 같은 기준이라
    서로 비교할 수 있다. Kepler는 CPU 세대에 따라 psys/package로 측정 범위가 갈려
    노드 간 비교가 불가능하다(CPU/RAM 계층은 클러스터·시스템 요약에서 별도 제공).

    DCMI가 기본이며, mlt만 BMC가 DCMI 명령을 지원하지 않는다(2026-08-26 확정).
    그 노드는 PSU 입력 전력 합으로 대체한다. 둘 다 벽면 전력이지만 산출 경로가 달라
    (compute5 실측 DCMI 417W vs PSU 입력 합 400W) source로 구분해 알린다.
    ipmi 메트릭에는 cluster 라벨이 없어 물리 `node` 라벨로 식별한다.
    """
    n = _esc(node)
    dcmi = await prometheus_client.instant(f'ipmi_dcmi_power_consumption_watts{{node="{n}"}}')
    if dcmi:
        return dcmi, "ipmi-dcmi"
    psu = await prometheus_client.instant(
        f'sum(ipmi_power_watts{{node="{n}",name=~".*(Power In|Input Power)"}})'
    )
    return psu, "ipmi-psu-input"


async def _ipmi_node_power_range(
    node: str, start: str, end: str, step: str
) -> tuple[list[dict], str]:
    """물리 노드 서버 총전력(시계열) — _ipmi_node_power_instant의 range 버전."""
    n = _esc(node)
    dcmi = await prometheus_client.range_query(
        f'ipmi_dcmi_power_consumption_watts{{node="{n}"}}', start, end, step
    )
    if dcmi:
        return dcmi, "ipmi-dcmi"
    psu = await prometheus_client.range_query(
        f'sum(ipmi_power_watts{{node="{n}",name=~".*(Power In|Input Power)"}})', start, end, step
    )
    return psu, "ipmi-psu-input"


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


@router.get("/clusters/{cluster}/nodes", summary="노드 목록", response_model=NodeListResponse)
async def list_nodes(
    request: Request,
    cluster: str,
    params: PaginationParams = Depends(),
    node_type: Optional[str] = Query(None, description='노드 타입 필터: "physical" | "virtual"'),
):
    """클러스터에 속한 노드 목록 조회

    - 노드 이름, 내부 IP, 역할(worker | control-plane), 소속 클러스터
    - 정상 동작 여부, 종류(physical | virtual)
    - OS 이미지, kubelet 버전
    - 이 노드의 가속기 카드 수, 전력(W)
    - total : 전체 노드 개수

    노드 이름은 하위 경로(상세, CPU, 메모리 등)의 식별자로 그대로 사용 가능.
    관리 클러스터는 Kubernetes에 등록된 노드가 기준이고, 가속기 클러스터는 가속기 메트릭에
    찍힌 호스트 이름이 기준.

    정렬: sort_by=power_watts 지원. 그 외 값은 이름순으로 정렬.
    """
    info = await _require_cluster(cluster)
    phys_nodes = await _phys_node_set()

    nodes: list[NodeSummaryItem]
    if info.type == "management":
        c = _cl(cluster)
        info_results = await prometheus_client.instant(f'kube_node_info{{cluster="{c}"}}')
        role_results = await prometheus_client.instant(f'kube_node_role{{cluster="{c}"}}')
        ready_results = await prometheus_client.instant(
            f'kube_node_status_condition{{cluster="{c}",condition="Ready",status="true"}}'
        )
        # ipmi 메트릭에는 cluster 라벨이 없어 전체 조회 후 node 라벨로 매칭한다.
        # 단건 조회(`/nodes/{node}/power`)와 같은 기준을 쓴다. DCMI가 기본이고, DCMI를
        # 지원하지 않는 노드(mlt)는 PSU 입력 전력 합으로 채운다.
        power_results = await prometheus_client.instant("ipmi_dcmi_power_consumption_watts")
        psu_results = await prometheus_client.instant(
            'sum by (node) (ipmi_power_watts{name=~".*(Power In|Input Power)"})'
        )

        role_map: dict[str, str] = {}
        for item in role_results:
            metric = item.get("metric", {})
            n = metric.get("node")
            role = metric.get("role")
            if n and role:
                role_map[n] = role

        ready_map: dict[str, bool] = {}
        for item in ready_results:
            metric = item.get("metric", {})
            n = metric.get("node")
            if not n:
                continue
            val = _first_value([item]) or 0.0
            ready_map[n] = ready_map.get(n, False) or val == 1.0

        power_map: dict[str, float] = {}
        for item in psu_results + power_results:  # DCMI가 나중에 덮어써 우선한다
            n = item.get("metric", {}).get("node")
            val = _first_value([item])
            if n and val is not None:
                power_map[n] = val

        # kube_node_info 1시계열 = k8s 노드 1대. (node, internal_ip, os, kubelet_version)로 수집.
        nodes = []
        for item in info_results:
            metric = item.get("metric", {})
            n = metric.get("node")
            if not n:
                continue
            nodes.append(
                NodeSummaryItem(
                    nodename=n,
                    internal_ip=metric.get("internal_ip"),
                    role=role_map.get(n),
                    cluster=cluster,
                    up=ready_map.get(n, False),
                    os=metric.get("os_image") or metric.get("kernel_version"),
                    kubelet_version=metric.get("kubelet_version"),
                    node_type="physical" if n in phys_nodes else "virtual",
                    power_watts=power_map.get(n),
                )
            )
    else:
        # 서비스 클러스터: kube_node_info가 없어 가속기 메트릭 hostname을 노드로 삼는다.
        host_rows = await _service_cluster_hosts(info, phys_nodes)
        nodes = [
            NodeSummaryItem(
                nodename=host,
                internal_ip=None,
                role=None,
                cluster=cluster,
                up=True,  # 가속기 메트릭이 보고되는 시점 = 살아있는 호스트
                os=None,
                kubelet_version=None,
                node_type="physical" if is_phys else "virtual",
                accelerator_count=accel_count,
                power_watts=power,
            )
            for host, is_phys, accel_count, power in host_rows
        ]

    if node_type:
        nodes = [n for n in nodes if n.node_type == node_type]

    if params.search:
        q = params.search.lower()
        nodes = [n for n in nodes if q in (n.nodename or "").lower() or q in (n.internal_ip or "").lower()]

    if params.sort_by == "power_watts":
        desc = params.sort_order == "desc"
        nodes.sort(key=lambda n: (n.power_watts is None, -(n.power_watts or 0.0) if desc else (n.power_watts or 0.0)))
    else:
        nodes.sort(key=lambda n: n.nodename or "")

    total = len(nodes)
    page = nodes[params.offset : params.offset + params.limit]

    warnings: list[str] = [] if total else ["NO_DATA"]

    return NodeListResponse(
        status="success" if total else "partial",
        nodes=page,
        total=total,
        warnings=warnings,
    )


@router.get("/clusters/{cluster}/nodes/summary", summary="노드 집계 요약", response_model=NodesSummaryResponse)
async def get_nodes_summary(request: Request, cluster: str):
    """클러스터 노드 전체를 하나로 합친 집계값 조회

    - ready_count : 정상 노드 수
    - total_count : 전체 노드 수
    - memory_total_bytes : 전체 메모리 합계(bytes)
    - memory_used_bytes : 사용 메모리 합계(bytes)
    - memory_usage_percent : 메모리 사용률(%)

    Kubernetes에 등록되지 않은 가속기 전용 클러스터는 노드 수가 0으로 나옴.
    """
    await _require_cluster(cluster)
    c = _cl(cluster)
    info_results = await prometheus_client.instant(f'kube_node_info{{cluster="{c}"}}')
    ready_results = await prometheus_client.instant(
        f'kube_node_status_condition{{cluster="{c}",condition="Ready",status="true"}}'
    )
    total_mem_results = await prometheus_client.instant(f'sum(node_memory_MemTotal_bytes{{cluster="{c}"}})')
    avail_mem_results = await prometheus_client.instant(f'sum(node_memory_MemAvailable_bytes{{cluster="{c}"}})')

    node_names: set[str] = set()
    for item in info_results:
        n = item.get("metric", {}).get("node")
        if n:
            node_names.add(n)

    ready_count = 0
    for item in ready_results:
        val = _first_value([item]) or 0.0
        if val == 1.0:
            ready_count += 1

    mem_total = _first_value(total_mem_results)
    mem_avail = _first_value(avail_mem_results)
    mem_used = (mem_total - mem_avail) if mem_total is not None and mem_avail is not None else None
    mem_pct = (mem_used / mem_total * 100) if mem_used is not None and mem_total else None

    warnings: list[str] = [] if info_results else ["NO_DATA"]

    data = NodesSummaryData(
        ready_count=ready_count,
        total_count=len(node_names),
        memory_total_bytes=mem_total,
        memory_used_bytes=mem_used,
        memory_usage_percent=mem_pct,
    )
    return NodesSummaryResponse(status="success" if info_results else "partial", data=data, warnings=warnings)


@router.get("/clusters/{cluster}/nodes/{node}", summary="노드 상세", response_model=NodeDetailResponse)
async def get_node(request: Request, cluster: str, node: str):
    """노드 한 대의 상세 조회

    - 노드 이름, 소속 클러스터, 메트릭 보고 여부
    - os : 커널 종류, 릴리스, 버전, 아키텍처, 호스트명
    - boot_time : 마지막 부팅 시각
    - cpu_cores : CPU 코어 수
    - memory_total_bytes : 전체 메모리(bytes)
    - node_type : physical | virtual
    - ready : Kubernetes Ready 상태 (관리 클러스터만 값이 있음)
    - accelerator_count : 이 노드의 가속기 카드 수
    """
    await _require_cluster(cluster)
    raw_node = node
    node = await _resolve_instance(cluster, node)
    label = f'cluster="{_cl(cluster)}",instance="{_esc(node)}"'

    up_results = await prometheus_client.instant(f"up{{{label}}}")
    uname_results = await prometheus_client.instant(f"node_uname_info{{{label}}}")
    accel_count = await _accelerator_count_for_host(raw_node)

    if not up_results and not uname_results and accel_count == 0:
        return NodeDetailResponse(status="partial", data=None, warnings=["NO_DATA"])

    boot_results = await prometheus_client.instant(f"node_boot_time_seconds{{{label}}}")
    mem_results = await prometheus_client.instant(f"node_memory_MemTotal_bytes{{{label}}}")

    phys_nodes = await _phys_node_set()
    cpu_val = _first_value(
        await prometheus_client.instant(f'machine_cpu_cores{{node="{_esc(raw_node)}"}}')
    )
    if cpu_val is None:
        cpu_val = _first_value(
            await prometheus_client.instant(f'count(node_cpu_seconds_total{{{label},mode="idle"}})')
        )

    ready_results = await prometheus_client.instant(
        f'kube_node_status_condition{{condition="Ready",status="true",node="{_esc(raw_node)}"}}'
    )
    ready = any(_first_value([item]) == 1.0 for item in ready_results) if ready_results else None

    up = any(_first_value([item]) == 1.0 for item in up_results)

    os_info = None
    if uname_results:
        metric = uname_results[0].get("metric", {})
        os_info = NodeOsInfo(
            sysname=metric.get("sysname"),
            release=metric.get("release"),
            version=metric.get("version"),
            machine=metric.get("machine"),
            nodename=metric.get("nodename"),
        )

    boot_val = _first_value(boot_results)
    boot_time = (
        datetime.fromtimestamp(boot_val, tz=timezone.utc).isoformat() if boot_val is not None else None
    )

    data = NodeDetailData(
        instance=node,
        cluster=cluster,
        up=up,
        os=os_info,
        boot_time=boot_time,
        cpu_cores=int(cpu_val) if cpu_val is not None else None,
        memory_total_bytes=_first_value(mem_results),
        node_type="physical" if raw_node in phys_nodes else "virtual",
        ready=ready,
        accelerator_count=accel_count,
    )
    return NodeDetailResponse(status="success", data=data, warnings=[])


@router.get(
    "/clusters/{cluster}/nodes/{node}/metrics", summary="노드 종합 메트릭", response_model=NodeMetricsResponse
)
async def get_node_metrics(request: Request, cluster: str, node: str):
    """노드 한 대의 주요 사용량을 한 번에 조회

    - cpu_usage_percent : CPU 사용률(%)
    - memory_usage_percent : 메모리 사용률(%), 전체 메모리와 사용 메모리(bytes)
    - disk_usage_percent : 실디스크 사용률(%), 용량 가중 합계 기준. tmpfs 등 가상 파일시스템 제외
    - network_receive_bytes_per_sec : 네트워크 수신 처리량(bytes/sec)
    - network_transmit_bytes_per_sec : 네트워크 송신 처리량(bytes/sec)
    """
    await _require_cluster(cluster)
    node = await _resolve_instance(cluster, node)
    label = f'cluster="{_cl(cluster)}",instance="{_esc(node)}"'

    cpu_results = await prometheus_client.instant(
        f'100 - (avg(rate(node_cpu_seconds_total{{{label},mode="idle"}}[5m])) * 100)'
    )
    mem_total_results = await prometheus_client.instant(f"node_memory_MemTotal_bytes{{{label}}}")
    mem_avail_results = await prometheus_client.instant(f"node_memory_MemAvailable_bytes{{{label}}}")
    # 용량 가중으로 계산한다. 파일시스템별 사용률을 avg()로 평균내면 1GB tmpfs가 500GB 디스크와
    # 같은 비중을 가져 실제 사용률이 희석된다(실측 27.9% → 9.2%). 또 `/run/user/*` tmpfs는
    # 로그인 수에 따라 늘어나 값이 흔들린다. REAL_FS로 실디스크만 남기면 크기 0인 ramfs의
    # 0/0 = NaN도 함께 걸러진다(NaN이 하나라도 섞이면 집계 전체가 NaN이 되어 null로 떨어짐).
    disk_label = f"{label},{REAL_FS}"
    disk_results = await prometheus_client.instant(
        f"(1 - sum(node_filesystem_avail_bytes{{{disk_label}}})"
        f" / sum(node_filesystem_size_bytes{{{disk_label}}})) * 100"
    )
    net_rx_results = await prometheus_client.instant(f"sum(rate(node_network_receive_bytes_total{{{label}}}[5m]))")
    net_tx_results = await prometheus_client.instant(f"sum(rate(node_network_transmit_bytes_total{{{label}}}[5m]))")

    mem_total = _first_value(mem_total_results)
    mem_avail = _first_value(mem_avail_results)
    mem_used = (mem_total - mem_avail) if mem_total is not None and mem_avail is not None else None
    mem_pct = (mem_used / mem_total * 100) if mem_used is not None and mem_total else None

    has_data = bool(cpu_results or mem_total_results or disk_results or net_rx_results)
    warnings: list[str] = [] if has_data else ["NO_DATA"]

    data = NodeMetricsData(
        cpu_usage_percent=_first_value(cpu_results),
        memory_usage_percent=mem_pct,
        memory_total_bytes=mem_total,
        memory_used_bytes=mem_used,
        disk_usage_percent=_first_value(disk_results),
        network_receive_bytes_per_sec=_first_value(net_rx_results),
        network_transmit_bytes_per_sec=_first_value(net_tx_results),
    )
    return NodeMetricsResponse(status="success" if has_data else "partial", data=data, warnings=warnings)


@router.get("/clusters/{cluster}/nodes/{node}/cpu", summary="노드 CPU 상세", response_model=NodeCpuResponse)
async def get_node_cpu(request: Request, cluster: str, node: str):
    """노드 한 대의 CPU 사용 상세 조회

    - usage_percent : 전체 CPU 사용률(%)
    - load1, load5, load15 : 1분, 5분, 15분 평균 부하
    - per_core : 코어별 사용률(%)
    - per_mode : 처리 종류(user, system, iowait 등)별 CPU 시간 비율
    """
    await _require_cluster(cluster)
    node = await _resolve_instance(cluster, node)
    label = f'cluster="{_cl(cluster)}",instance="{_esc(node)}"'

    usage_results = await prometheus_client.instant(
        f'100 - (avg(rate(node_cpu_seconds_total{{{label},mode="idle"}}[5m])) * 100)'
    )
    per_core_results = await prometheus_client.instant(
        f'100 - (avg by (cpu) (rate(node_cpu_seconds_total{{{label},mode="idle"}}[5m])) * 100)'
    )
    per_mode_results = await prometheus_client.instant(f"sum by (mode) (rate(node_cpu_seconds_total{{{label}}}[5m]))")
    load1_results = await prometheus_client.instant(f"node_load1{{{label}}}")
    load5_results = await prometheus_client.instant(f"node_load5{{{label}}}")
    load15_results = await prometheus_client.instant(f"node_load15{{{label}}}")

    per_core = [
        CpuCoreUsage(cpu=item.get("metric", {}).get("cpu", "?"), usage_percent=_first_value([item]) or 0.0)
        for item in per_core_results
    ]
    per_mode = [
        CpuModeBreakdown(mode=item.get("metric", {}).get("mode", "?"), rate=_first_value([item]) or 0.0)
        for item in per_mode_results
    ]

    has_data = bool(usage_results or per_core_results)
    warnings: list[str] = [] if has_data else ["NO_DATA"]

    data = NodeCpuData(
        usage_percent=_first_value(usage_results),
        load1=_first_value(load1_results),
        load5=_first_value(load5_results),
        load15=_first_value(load15_results),
        per_core=per_core,
        per_mode=per_mode,
    )
    return NodeCpuResponse(status="success" if has_data else "partial", data=data, warnings=warnings)


@router.get("/clusters/{cluster}/nodes/{node}/memory", summary="노드 메모리 상세", response_model=NodeMemoryResponse)
async def get_node_memory(request: Request, cluster: str, node: str):
    """노드 한 대의 메모리 사용 상세 조회

    - total_bytes, available_bytes, used_bytes : 전체, 가용, 사용 메모리(bytes)
    - used_percent : 메모리 사용률(%)
    - cached_bytes, buffers_bytes : 캐시와 버퍼가 차지한 메모리(bytes)
    - swap_total_bytes, swap_free_bytes, swap_used_bytes : 스왑 전체, 가용, 사용량(bytes)
    """
    await _require_cluster(cluster)
    node = await _resolve_instance(cluster, node)
    label = f'cluster="{_cl(cluster)}",instance="{_esc(node)}"'

    total_results = await prometheus_client.instant(f"node_memory_MemTotal_bytes{{{label}}}")
    avail_results = await prometheus_client.instant(f"node_memory_MemAvailable_bytes{{{label}}}")
    cached_results = await prometheus_client.instant(f"node_memory_Cached_bytes{{{label}}}")
    buffers_results = await prometheus_client.instant(f"node_memory_Buffers_bytes{{{label}}}")
    swap_total_results = await prometheus_client.instant(f"node_memory_SwapTotal_bytes{{{label}}}")
    swap_free_results = await prometheus_client.instant(f"node_memory_SwapFree_bytes{{{label}}}")

    total = _first_value(total_results)
    avail = _first_value(avail_results)
    used = (total - avail) if total is not None and avail is not None else None
    used_pct = (used / total * 100) if used is not None and total else None
    swap_total = _first_value(swap_total_results)
    swap_free = _first_value(swap_free_results)
    swap_used = (swap_total - swap_free) if swap_total is not None and swap_free is not None else None

    warnings: list[str] = [] if total_results else ["NO_DATA"]

    data = NodeMemoryData(
        total_bytes=total,
        available_bytes=avail,
        used_bytes=used,
        used_percent=used_pct,
        cached_bytes=_first_value(cached_results),
        buffers_bytes=_first_value(buffers_results),
        swap_total_bytes=swap_total,
        swap_free_bytes=swap_free,
        swap_used_bytes=swap_used,
    )
    return NodeMemoryResponse(status="success" if total_results else "partial", data=data, warnings=warnings)


@router.get(
    "/clusters/{cluster}/nodes/{node}/storage", summary="노드 로컬 디스크 상세", response_model=NodeStorageResponse
)
async def get_node_storage(request: Request, cluster: str, node: str):
    """노드 한 대에 붙어 있는 디스크 사용 상세 조회

    - filesystems : 마운트 위치별 파일시스템 종류, 전체 용량, 남은 용량(bytes), 사용률(%)
    - disks : 디스크 장치별 읽기, 쓰기 처리량(bytes/sec)

    Ceph 분산 스토리지는 이 경로가 아니라 클러스터의 storage 경로에서 조회.
    """
    await _require_cluster(cluster)
    node = await _resolve_instance(cluster, node)
    label = f'cluster="{_cl(cluster)}",instance="{_esc(node)}"'

    size_results = await prometheus_client.instant(f"node_filesystem_size_bytes{{{label}}}")
    avail_results = await prometheus_client.instant(f"node_filesystem_avail_bytes{{{label}}}")
    read_results = await prometheus_client.instant(f"rate(node_disk_read_bytes_total{{{label}}}[5m])")
    write_results = await prometheus_client.instant(f"rate(node_disk_written_bytes_total{{{label}}}[5m])")

    avail_map: dict[str, float] = {}
    for item in avail_results:
        mp = item.get("metric", {}).get("mountpoint")
        val = _first_value([item])
        if mp and val is not None:
            avail_map[mp] = val

    filesystems = []
    for item in size_results:
        metric = item.get("metric", {})
        mp = metric.get("mountpoint", "?")
        size = _first_value([item])
        avail = avail_map.get(mp)
        used_pct = (1 - avail / size) * 100 if size and avail is not None else None
        filesystems.append(
            FilesystemUsage(mountpoint=mp, fstype=metric.get("fstype"), size_bytes=size, avail_bytes=avail, used_percent=used_pct)
        )

    write_map: dict[str, float] = {}
    for item in write_results:
        device = item.get("metric", {}).get("device")
        val = _first_value([item])
        if device and val is not None:
            write_map[device] = val

    disks = [
        DiskIoRate(
            device=item.get("metric", {}).get("device", "?"),
            read_bytes_per_sec=_first_value([item]),
            write_bytes_per_sec=write_map.get(item.get("metric", {}).get("device")),
        )
        for item in read_results
    ]

    warnings: list[str] = [] if size_results else ["NO_DATA"]

    data = NodeStorageData(filesystems=filesystems, disks=disks)
    return NodeStorageResponse(status="success" if size_results else "partial", data=data, warnings=warnings)


@router.get(
    "/clusters/{cluster}/nodes/{node}/network", summary="노드 네트워크 상세", response_model=NodeNetworkResponse
)
async def get_node_network(request: Request, cluster: str, node: str):
    """노드 한 대의 네트워크 사용 상세 조회

    - interfaces : 네트워크 장치별 수신, 송신 처리량(bytes/sec)
    - 장치별 수신, 송신 에러 발생 건수(errors/sec)
    """
    await _require_cluster(cluster)
    node = await _resolve_instance(cluster, node)
    label = f'cluster="{_cl(cluster)}",instance="{_esc(node)}"'

    rx_results = await prometheus_client.instant(f"rate(node_network_receive_bytes_total{{{label}}}[5m])")
    tx_results = await prometheus_client.instant(f"rate(node_network_transmit_bytes_total{{{label}}}[5m])")
    rx_err_results = await prometheus_client.instant(f"rate(node_network_receive_errs_total{{{label}}}[5m])")
    tx_err_results = await prometheus_client.instant(f"rate(node_network_transmit_errs_total{{{label}}}[5m])")

    def _by_device(results: list[dict]) -> dict[str, Optional[float]]:
        out: dict[str, Optional[float]] = {}
        for item in results:
            dev = item.get("metric", {}).get("device")
            if dev:
                out[dev] = _first_value([item])
        return out

    tx_map = _by_device(tx_results)
    rx_err_map = _by_device(rx_err_results)
    tx_err_map = _by_device(tx_err_results)

    interfaces = [
        NetworkInterfaceStats(
            device=item.get("metric", {}).get("device", "?"),
            receive_bytes_per_sec=_first_value([item]),
            transmit_bytes_per_sec=tx_map.get(item.get("metric", {}).get("device")),
            receive_errors_per_sec=rx_err_map.get(item.get("metric", {}).get("device")),
            transmit_errors_per_sec=tx_err_map.get(item.get("metric", {}).get("device")),
        )
        for item in rx_results
    ]

    warnings: list[str] = [] if rx_results else ["NO_DATA"]

    data = NodeNetworkData(interfaces=interfaces)
    return NodeNetworkResponse(status="success" if rx_results else "partial", data=data, warnings=warnings)


# ---------------------------------------------------------------------------
# Power
# ---------------------------------------------------------------------------


@router.get(
    "/clusters/{cluster}/nodes/{node}/power", summary="노드 전력 현재값", response_model=NodePowerResponse
)
async def get_node_power(request: Request, cluster: str, node: str):
    """노드 한 대가 지금 쓰고 있는 서버 총전력 조회

    - watts : 전력(W)
    - source : 산출 경로. `ipmi-dcmi`(BMC의 DCMI 명령) 또는 `ipmi-psu-input`(PSU 입력 전력 합)

    BMC가 전원공급장치를 읽은 벽면 전력. CPU·메모리뿐 아니라 가속기·팬·디스크·PSU 손실까지
    포함하며, 노드 간 비교에 쓸 수 있는 단일 기준. CPU/메모리 계층만 따로 보려면
    클러스터 요약(`/clusters/{cluster}/summary`)의 `power.breakdown.cpu_watts` 사용.
    BMC가 없는 가상 노드는 NO_POWER_DATA 경고와 함께 빈 데이터 반환.
    """
    await _require_cluster(cluster)
    results, source = await _ipmi_node_power_instant(node)

    if not results:
        return NodePowerResponse(status="partial", data=None, warnings=["NO_POWER_DATA"])

    data = NodePowerData(watts=_first_value(results), source=source)
    return NodePowerResponse(status="success", data=data, warnings=[])


@router.get(
    "/clusters/{cluster}/nodes/{node}/power/timeseries",
    summary="노드 전력 시계열",
    response_model=NodePowerTimeseriesResponse,
)
async def get_node_power_timeseries(
    request: Request, cluster: str, node: str, params: TimeseriesParams = Depends()
):
    """노드 한 대의 서버 총전력 변화 추이 조회

    - series : (시각, 전력값(W)) 쌍 목록
    - source : 산출 경로. `ipmi-dcmi` 또는 `ipmi-psu-input`
    - 조회 기간과 간격은 period, start, end, step 파라미터로 지정

    현재값(`/power`)과 같은 IPMI 기준.
    """
    await _require_cluster(cluster)
    now = datetime.now(timezone.utc)
    start = params.start or (now - timedelta(hours=1)).isoformat()
    end = params.end or now.isoformat()
    step = params.step

    results, source = await _ipmi_node_power_range(node, start, end, step)

    if not results:
        return NodePowerTimeseriesResponse(status="partial", series=[], warnings=["NO_POWER_DATA"])

    series = [
        NodePowerTimeseriesPoint(
            timestamp=datetime.fromtimestamp(float(v[0]), tz=timezone.utc).isoformat(),
            watts=float(v[1]),
        )
        for v in results[0].get("values", [])
    ]
    return NodePowerTimeseriesResponse(status="success", series=series, source=source, warnings=[])


# ---------------------------------------------------------------------------
# Hardware (IPMI — 물리 노드 전용)
# ---------------------------------------------------------------------------


@router.get(
    "/clusters/{cluster}/nodes/{node}/hardware/sensors",
    summary="하드웨어 센서 전체(IPMI)",
    response_model=HardwareSensorsResponse,
)
async def get_hardware_sensors(request: Request, cluster: str, node: str):
    """서버 본체에 달린 하드웨어 센서 값 전체 조회

    - sensors : 센서 이름, 값, 단위
    - 온도, 팬 회전수, 전압, 전력 센서가 함께 나옴

    IPMI 센서를 걷어오지 않는 노드는 빈 목록 + IPMI_NOT_AVAILABLE 경고 반환.
    """
    await _require_cluster(cluster)
    results = await prometheus_client.instant(f'{{__name__=~"ipmi_.*",node="{_esc(node)}"}}')

    if not results:
        return HardwareSensorsResponse(status="partial", sensors=[], warnings=["IPMI_NOT_AVAILABLE"])

    sensors = []
    for item in results:
        value = _first_value([item])
        if value is None:  # NaN 센서(미적용/판독불가) 제외 — JSON nan 방지
            continue
        metric = item.get("metric", {})
        sensors.append(
            HardwareSensor(
                name=metric.get("name") or metric.get("__name__", "?"),
                value=value,
                unit=metric.get("unit"),
            )
        )
    return HardwareSensorsResponse(status="success", sensors=sensors, warnings=[])


@router.get(
    "/clusters/{cluster}/nodes/{node}/hardware/power",
    summary="서버 본체 전력 실측",
    response_model=HardwarePowerResponse,
)
async def get_hardware_power(request: Request, cluster: str, node: str):
    """서버 한 대가 콘센트에서 끌어가는 전체 전력 조회

    - watts : 전력(W)

    서버 관리 칩(BMC)이 직접 측정한 값으로, CPU와 가속기뿐 아니라 팬과 메모리까지 포함.
    IPMI 센서를 걷어오지 않는 노드는 IPMI_NOT_AVAILABLE 경고 반환.
    """
    await _require_cluster(cluster)
    results = await prometheus_client.instant(
        f'ipmi_dcmi_power_consumption_watts{{node="{_esc(node)}"}}'
    )

    if not results:
        return HardwarePowerResponse(status="partial", watts=None, warnings=["IPMI_NOT_AVAILABLE"])

    return HardwarePowerResponse(status="success", watts=_first_value(results), warnings=[])


@router.get(
    "/clusters/{cluster}/nodes/{node}/hardware/temperature",
    summary="하드웨어 온도(IPMI)",
    response_model=HardwareTemperatureResponse,
)
async def get_hardware_temperature(request: Request, cluster: str, node: str):
    """서버 본체 온도 센서 값 조회

    - sensors : 센서 이름, 온도(°C)
    - CPU 흡기, 배기, 메인보드 등 위치별 온도가 나옴

    IPMI 센서를 걷어오지 않는 노드는 빈 목록 + IPMI_NOT_AVAILABLE 경고 반환.
    """
    await _require_cluster(cluster)
    results = await prometheus_client.instant(f'ipmi_temperature_celsius{{node="{_esc(node)}"}}')

    if not results:
        return HardwareTemperatureResponse(status="partial", sensors=[], warnings=["IPMI_NOT_AVAILABLE"])

    sensors = [
        HardwareTemperatureSensor(name=item.get("metric", {}).get("name", "?"), celsius=_first_value([item]) or 0.0)
        for item in results
    ]
    return HardwareTemperatureResponse(status="success", sensors=sensors, warnings=[])
