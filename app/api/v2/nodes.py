"""
KCloud Monitor v2 — Nodes (10개) + Hardware/IPMI (3개).

노드 = 관리 클러스터의 물리 서버 또는 서비스 클러스터의 VM 노드(node_type으로 구분).
hardware/* 는 물리 노드 전용(IPMI BMC 실측) — VM 노드 호출 시 구현에서 4xx 예정.
데이터소스(구현 예정): Mimir(node-exporter, kepler, ipmi-exporter), kube-state-metrics.
설계: sample_api.md §3.1~§3.6 / 전력 계층 P2(Kepler)·P3(IPMI), 메트릭 M1.
"""
from fastapi import APIRouter, Depends, Request

from app.api.v2._stub import stub
from app.api.v2.deps import list_params, timeseries_params

router = APIRouter()

# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


@router.get("/clusters/{cluster}/nodes", summary="노드 목록")
async def list_nodes(request: Request, cluster: str, params: dict = Depends(list_params)):
    """노드 목록 — 물리 서버(관리) 또는 VM 노드(서비스). node_type=physical|virtual 구분. [§3.1, §3.2]"""
    return stub(
        request,
        "노드 목록(역할·상태·자원 용량·node_type)",
        sources=("Mimir(kube_node_info, node_uname_info)", "kube-state-metrics"),
        ref="sample_api.md §3.1, §3.2",
        params=params,
    )


@router.get("/clusters/{cluster}/nodes/summary", summary="노드 집계 요약")
async def get_nodes_summary(request: Request, cluster: str):
    """노드 집계 — Ready/NotReady 수, 역할 분포, 자원 총량/사용량 합계."""
    return stub(
        request,
        "노드 집계 요약(상태·역할·용량 합계)",
        sources=("Mimir(kube_node_status_condition)",),
    )


@router.get("/clusters/{cluster}/nodes/{node}", summary="노드 상세")
async def get_node(request: Request, cluster: str, node: str):
    """단일 노드 상세 — 하드웨어 스펙, K8s 라벨/테인트, 가속기 장착 요약."""
    return stub(
        request,
        "노드 상세(스펙·라벨·가속기 요약)",
        sources=("Mimir(kube_node_info, node_*)", "resource-map 원장(물리 서버 연계)"),
    )


@router.get("/clusters/{cluster}/nodes/{node}/metrics", summary="노드 종합 메트릭 [M1]")
async def get_node_metrics(request: Request, cluster: str, node: str):
    """노드 종합 메트릭 — CPU/메모리/디스크/네트워크 현재값. 메트릭 계층 M1. [§3.3]"""
    return stub(
        request,
        "노드 종합 메트릭(CPU/MEM/Disk/Net)",
        sources=("Mimir(node_cpu_seconds_total, node_memory_*, node_disk_*, node_network_*)",),
        ref="sample_api.md §3.3",
    )


@router.get("/clusters/{cluster}/nodes/{node}/power", summary="노드 전력 현재값 [P2]")
async def get_node_power(request: Request, cluster: str, node: str):
    """노드 전력 현재값 — Kepler RAPL 실측(물리) 또는 귀속 추정(VM). 전력 계층 P2. [§3.4]"""
    return stub(
        request,
        "노드 전력 현재값(P2, measured_rapl 또는 attributed)",
        sources=("Mimir(kepler_node_cpu_watts)", "power_attribution_plan §7 신뢰도 표기"),
        ref="sample_api.md §3.4",
    )


@router.get("/clusters/{cluster}/nodes/{node}/power/timeseries", summary="노드 전력 시계열")
async def get_node_power_timeseries(
    request: Request, cluster: str, node: str, params: dict = Depends(timeseries_params)
):
    """노드 전력 시계열 — period/step 기반 구간 조회. [§3.5]"""
    return stub(
        request,
        "노드 전력 시계열",
        sources=("Mimir(kepler_node_cpu_joules_total rate)",),
        ref="sample_api.md §3.5",
        params=params,
    )


@router.get("/clusters/{cluster}/nodes/{node}/cpu", summary="노드 CPU 상세")
async def get_node_cpu(request: Request, cluster: str, node: str):
    """노드 CPU 상세 — 코어별 사용률, load average, 모드별 분해."""
    return stub(request, "노드 CPU 상세", sources=("Mimir(node_cpu_seconds_total, node_load*)",))


@router.get("/clusters/{cluster}/nodes/{node}/memory", summary="노드 메모리 상세")
async def get_node_memory(request: Request, cluster: str, node: str):
    """노드 메모리 상세 — total/available/cached/buffers, 스왑."""
    return stub(request, "노드 메모리 상세", sources=("Mimir(node_memory_*)",))


@router.get("/clusters/{cluster}/nodes/{node}/storage", summary="노드 로컬 디스크 상세")
async def get_node_storage(request: Request, cluster: str, node: str):
    """노드 로컬 디스크 — 파일시스템 용량/사용률, 디스크 I/O. (Ceph 분산 스토리지는 /clusters/{c}/storage/*)"""
    return stub(request, "노드 로컬 디스크 상세", sources=("Mimir(node_filesystem_*, node_disk_*)",))


@router.get("/clusters/{cluster}/nodes/{node}/network", summary="노드 네트워크 상세")
async def get_node_network(request: Request, cluster: str, node: str):
    """노드 네트워크 — 인터페이스별 수신/송신 처리량, 에러/드롭."""
    return stub(request, "노드 네트워크 상세", sources=("Mimir(node_network_*)",))


# ---------------------------------------------------------------------------
# Hardware (IPMI — 물리 노드 전용)
# ---------------------------------------------------------------------------


@router.get("/clusters/{cluster}/nodes/{node}/hardware/sensors", summary="하드웨어 센서 전체(IPMI)")
async def get_hardware_sensors(request: Request, cluster: str, node: str):
    """IPMI 센서 전체 — 전력/온도/팬/전압/전류. 물리 노드 전용. [§3.6]"""
    return stub(
        request,
        "IPMI 하드웨어 센서 전체(물리 노드 전용)",
        sources=("Mimir(ipmi_* — BMC 계정 확보로 ipmi-exporter 수집, 2026-08)",),
        ref="sample_api.md §3.6",
    )


@router.get("/clusters/{cluster}/nodes/{node}/hardware/power", summary="BMC 전력 실측 [P3]")
async def get_hardware_power(request: Request, cluster: str, node: str):
    """서버 전체 전력 BMC 실측(DCMI) — measured_ipmi/direct. 전력 계층 P3. [§3.6]"""
    return stub(
        request,
        "BMC 전력 실측(P3, measured_ipmi)",
        sources=("Mimir(ipmi_dcmi_power_consumption_current_watts)",),
        ref="sfr_api_mapping.md OPT.002 P3",
    )


@router.get("/clusters/{cluster}/nodes/{node}/hardware/temperature", summary="하드웨어 온도(IPMI)")
async def get_hardware_temperature(request: Request, cluster: str, node: str):
    """IPMI 온도 센서 — 흡기/배기/CPU 등 센서별 온도와 임계 상태. [§3.6]"""
    return stub(
        request,
        "IPMI 온도 센서",
        sources=("Mimir(ipmi_temperature_celsius)",),
        ref="sample_api.md §3.6",
    )
