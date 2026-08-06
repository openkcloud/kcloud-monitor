"""
KCloud Monitor v2 — Storage/Ceph (S1~S10, 10개).

Rook-Ceph 분산 스토리지 모니터링(v2 신규 도메인, 2026-06-22 확정).
노드 로컬 디스크(/clusters/{c}/nodes/{n}/storage)와 구분된다.
데이터소스(구현 예정): Mimir(ceph_* — rook-ceph-mgr:9283 + rook-ceph-exporter:9926).
설계: docs/temp/01-domain-plans/openkcloud_storage_ceph_plan.md §2(S1~S10)·§3(모델)·§4(메트릭).
"""
from fastapi import APIRouter, Depends, Request

from app.api.v2._stub import stub
from app.api.v2.deps import list_params, timeseries_params

router = APIRouter()

CEPH_PLAN = "openkcloud_storage_ceph_plan.md"


@router.get("/clusters/{cluster}/storage/ceph/summary", summary="Ceph 요약 [S1]")
async def get_ceph_summary(request: Request, cluster: str):
    """Ceph 클러스터 요약 — health, OSD up/in, 용량·사용률, 풀 수. 모델: CephSummary."""
    return stub(
        request,
        "Ceph 요약(health·OSD·용량·풀 수)",
        sources=("Mimir(ceph_health_status, ceph_osd_up/in, ceph_cluster_total_*)",),
        ref=f"{CEPH_PLAN} S1",
    )


@router.get("/clusters/{cluster}/storage/ceph/health", summary="Ceph health 상세 [S2]")
async def get_ceph_health(request: Request, cluster: str):
    """Ceph health — 상태 코드(HEALTH_OK/WARN/ERR)와 체크 항목 상세."""
    return stub(
        request,
        "Ceph health 상태(코드+체크 상세)",
        sources=("Mimir(ceph_health_status, ceph_health_detail)",),
        ref=f"{CEPH_PLAN} S2",
    )


@router.get("/clusters/{cluster}/storage/ceph/capacity", summary="Ceph 용량 [S3]")
async def get_ceph_capacity(request: Request, cluster: str):
    """Ceph 용량 — total/used/avail, device-class별 분해. 모델: CephCapacity."""
    return stub(
        request,
        "Ceph 용량(total/used/avail, device-class별)",
        sources=("Mimir(ceph_cluster_total_bytes, ceph_cluster_total_used_bytes)",),
        ref=f"{CEPH_PLAN} S3",
    )


@router.get("/clusters/{cluster}/storage/ceph/capacity/timeseries", summary="Ceph 용량 시계열 [S9]")
async def get_ceph_capacity_timeseries(
    request: Request, cluster: str, params: dict = Depends(timeseries_params)
):
    """Ceph 용량/사용률 시계열."""
    return stub(
        request,
        "Ceph 용량·사용률 시계열",
        sources=("Mimir(ceph_cluster_total_used_bytes range)",),
        ref=f"{CEPH_PLAN} S9",
        params=params,
    )


@router.get("/clusters/{cluster}/storage/ceph/osds", summary="OSD 목록 [S4]")
async def list_ceph_osds(request: Request, cluster: str, params: dict = Depends(list_params)):
    """OSD 목록 — up/in 상태, 용량, apply/commit latency. 모델: CephOSD."""
    return stub(
        request,
        "OSD 목록(상태·용량·latency)",
        sources=("Mimir(ceph_osd_up/in, ceph_osd_metadata, ceph_osd_*_latency_ms)",),
        ref=f"{CEPH_PLAN} S4",
        params=params,
    )


@router.get("/clusters/{cluster}/storage/ceph/osds/{osd_id}", summary="OSD 상세 [S5]")
async def get_ceph_osd(request: Request, cluster: str, osd_id: str):
    """단일 OSD 상세 — 소속 노드/디바이스, 상태, 사용량, latency."""
    return stub(
        request,
        "OSD 상세",
        sources=("Mimir(ceph_osd_metadata, ceph_osd_stat_*)",),
        ref=f"{CEPH_PLAN} S5",
    )


@router.get("/clusters/{cluster}/storage/ceph/pools", summary="풀 목록 [S6]")
async def list_ceph_pools(request: Request, cluster: str, params: dict = Depends(list_params)):
    """풀 목록 — stored/avail/objects, 읽기/쓰기 IOPS. 모델: CephPool."""
    return stub(
        request,
        "Ceph 풀 목록(stored/avail/objects/IOPS)",
        sources=("Mimir(ceph_pool_metadata, ceph_pool_stored, ceph_pool_rd/wr)",),
        ref=f"{CEPH_PLAN} S6",
        params=params,
    )


@router.get("/clusters/{cluster}/storage/ceph/pools/{pool}", summary="풀 상세 [S7]")
async def get_ceph_pool(request: Request, cluster: str, pool: str):
    """단일 풀 상세 — 용량/오브젝트/IOPS, replica/EC 구성."""
    return stub(
        request,
        "Ceph 풀 상세",
        sources=("Mimir(ceph_pool_*)",),
        ref=f"{CEPH_PLAN} S7",
    )


@router.get("/clusters/{cluster}/storage/ceph/pgs", summary="PG 상태 요약 [S8]")
async def get_ceph_pgs(request: Request, cluster: str):
    """Placement Group 상태 — total/active/clean/degraded 집계."""
    return stub(
        request,
        "PG 상태 요약(active/clean/degraded)",
        sources=("Mimir(ceph_pg_total, ceph_pg_active, ceph_pg_clean, ceph_pg_degraded)",),
        ref=f"{CEPH_PLAN} S8",
    )


@router.get("/clusters/{cluster}/storage/summary", summary="스토리지 통합 요약 [S10]")
async def get_storage_summary(request: Request, cluster: str):
    """스토리지 통합 요약 — Ceph + 향후 다른 백엔드(Cinder 등) 확장 지점."""
    return stub(
        request,
        "스토리지 통합 요약(백엔드 확장 지점)",
        sources=("Mimir(ceph_*)",),
        ref=f"{CEPH_PLAN} S10",
    )
