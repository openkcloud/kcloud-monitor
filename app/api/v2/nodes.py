"""
KCloud Monitor v2 — Nodes (10개) + Hardware/IPMI (3개).

node_exporter 메트릭 기반 노드 상세 조회. hardware/* 는 IPMI 미수집 시
"IPMI_NOT_AVAILABLE" 경고와 함께 빈 데이터를 반환한다.
데이터소스: Prometheus(node_exporter, kepler, ipmi-exporter).
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


async def _kepler_node_power_instant(node: str) -> list[dict]:
    """물리 노드 Kepler 전력(현재값) — zone=psys 우선, 없으면 package(+dram) 합산.

    Kepler zone은 psys ⊇ package ⊇ core 포함관계라 합산 시 중복 계산된다. psys가 있으면
    그것만 쓰고, 없는 노드(마스터 등)는 package(+dram)을 합산한다. 물리 노드는 `node_name`
    라벨로 식별하며 kepler/ipmi 메트릭에는 cluster 라벨이 없다.
    """
    n = _esc(node)
    psys = await prometheus_client.instant(
        f'sum(kepler_node_cpu_watts{{node_name="{n}",zone="psys"}})'
    )
    if psys:
        return psys
    return await prometheus_client.instant(
        f'sum(kepler_node_cpu_watts{{node_name="{n}",zone=~"package|dram"}})'
    )


async def _kepler_node_power_range(node: str, start: str, end: str, step: str) -> list[dict]:
    """물리 노드 Kepler 전력(시계열) — _kepler_node_power_instant의 range 버전."""
    n = _esc(node)
    psys = await prometheus_client.range_query(
        f'sum(kepler_node_cpu_watts{{node_name="{n}",zone="psys"}})', start, end, step
    )
    if psys:
        return psys
    return await prometheus_client.range_query(
        f'sum(kepler_node_cpu_watts{{node_name="{n}",zone=~"package|dram"}})', start, end, step
    )


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
    """노드 목록.

    관리 클러스터(mgmt)는 쿠버네티스 공식 노드(kube_node_info) 기준. node(이름, 예: compute1)를
    식별자로 삼으며 이 이름은 상세/서브 엔드포인트에도 그대로 쓸 수 있다(내부적으로 node-exporter
    instance로 자동 해석됨). 역할은 kube_node_role, Ready 상태는 kube_node_status_condition에서 가져온다.

    서비스 클러스터(l40s/rebellions/k8s-furiosa-rngd 등)는 kube_node_info가 없어, 대신 가속기
    메트릭의 hostname을 노드로 삼는다(node = 가속기가 보고된 VM/워커 호스트명).

    node_type은 machine_cpu_cores의 node 라벨 집합에 있으면 physical, 없으면 virtual이다.
    sort_by=power_watts 정렬을 지원한다(그 외 값은 이름순 기본 정렬로 폴백).
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
        # ipmi/kepler는 cluster 라벨이 없어 전체 조회 후 node 라벨로 매칭한다.
        power_results = await prometheus_client.instant("ipmi_dcmi_power_consumption_watts")

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
        for item in power_results:
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
    """노드 집계 — Ready/Total 수(kube_node_info/kube_node_status_condition 기준), 메모리 총량/사용량.

    가속기 전용 클러스터는 kube_node_info가 없어 0/0이 되는 것이 의도된 동작이다(클러스터 API와 동일 규약).
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
    """노드 상세 — OS 정보, 부팅 시각, 메모리 총량, CPU 코어 수, Ready 상태, 가속기 수.

    cpu_cores는 machine_cpu_cores(node 라벨, 물리 노드만 보고)를 우선 쓰고, 없으면
    node_cpu_seconds_total 코어 카운트로 폴백한다(가상 노드 등 machine_cpu_cores 미보고 시).
    ready는 kube_node_status_condition(mgmt만 존재), 가속기 수는 hostname 매칭(전 클러스터 공통)이다.
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
    "/clusters/{cluster}/nodes/{node}/metrics", summary="노드 종합 메트릭 [M1]", response_model=NodeMetricsResponse
)
async def get_node_metrics(request: Request, cluster: str, node: str):
    """노드 종합 메트릭 — CPU/메모리/디스크 사용률과 네트워크 처리량."""
    await _require_cluster(cluster)
    node = await _resolve_instance(cluster, node)
    label = f'cluster="{_cl(cluster)}",instance="{_esc(node)}"'

    cpu_results = await prometheus_client.instant(
        f'100 - (avg(rate(node_cpu_seconds_total{{{label},mode="idle"}}[5m])) * 100)'
    )
    mem_total_results = await prometheus_client.instant(f"node_memory_MemTotal_bytes{{{label}}}")
    mem_avail_results = await prometheus_client.instant(f"node_memory_MemAvailable_bytes{{{label}}}")
    disk_results = await prometheus_client.instant(
        f"avg(1 - (node_filesystem_avail_bytes{{{label}}} / node_filesystem_size_bytes{{{label}}})) * 100"
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
    """노드 CPU 상세 — 전체/코어별 사용률, load average, 모드별 사용량."""
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
    """노드 메모리 상세 — total/available/cached/buffers, 스왑."""
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
    """노드 로컬 디스크 — 마운트포인트별 용량/사용률과 디스크 I/O. (Ceph 분산 스토리지는 /clusters/{c}/storage/*)"""
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
    """노드 네트워크 — 인터페이스별 수신/송신 처리량과 에러율."""
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
    "/clusters/{cluster}/nodes/{node}/power", summary="노드 전력 현재값 [P2]", response_model=NodePowerResponse
)
async def get_node_power(request: Request, cluster: str, node: str):
    """노드 전력 현재값 — Kepler(RAPL) 실측. zone=psys 우선, 없으면 package(+dram)."""
    await _require_cluster(cluster)
    results = await _kepler_node_power_instant(node)

    if not results:
        return NodePowerResponse(status="partial", data=None, warnings=["NO_POWER_DATA"])

    data = NodePowerData(watts=_first_value(results), source="kepler")
    return NodePowerResponse(status="success", data=data, warnings=[])


@router.get(
    "/clusters/{cluster}/nodes/{node}/power/timeseries",
    summary="노드 전력 시계열",
    response_model=NodePowerTimeseriesResponse,
)
async def get_node_power_timeseries(
    request: Request, cluster: str, node: str, params: TimeseriesParams = Depends()
):
    """노드 전력 시계열 — Kepler(RAPL) 실측. zone=psys 우선, 없으면 package(+dram)."""
    await _require_cluster(cluster)
    now = datetime.now(timezone.utc)
    start = params.start or (now - timedelta(hours=1)).isoformat()
    end = params.end or now.isoformat()
    step = params.step

    results = await _kepler_node_power_range(node, start, end, step)

    if not results:
        return NodePowerTimeseriesResponse(status="partial", series=[], warnings=["NO_POWER_DATA"])

    series = [
        NodePowerTimeseriesPoint(
            timestamp=datetime.fromtimestamp(float(v[0]), tz=timezone.utc).isoformat(),
            watts=float(v[1]),
        )
        for v in results[0].get("values", [])
    ]
    return NodePowerTimeseriesResponse(status="success", series=series, warnings=[])


# ---------------------------------------------------------------------------
# Hardware (IPMI — 물리 노드 전용)
# ---------------------------------------------------------------------------


@router.get(
    "/clusters/{cluster}/nodes/{node}/hardware/sensors",
    summary="하드웨어 센서 전체(IPMI)",
    response_model=HardwareSensorsResponse,
)
async def get_hardware_sensors(request: Request, cluster: str, node: str):
    """IPMI 센서 전체 — 미수집 환경에서는 사용 불가로 응답."""
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
    summary="BMC 전력 실측 [P3]",
    response_model=HardwarePowerResponse,
)
async def get_hardware_power(request: Request, cluster: str, node: str):
    """서버 전체 전력 BMC 실측(DCMI) — 미수집 환경에서는 사용 불가로 응답."""
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
    """IPMI 온도 센서 — 미수집 환경에서는 사용 불가로 응답.

    ipmi_temperature_celsius는 cluster 라벨이 없고 node(호스트명)로 식별된다
    (hardware/power와 동일 라벨 규약 — probed: node="master3" 등).
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
