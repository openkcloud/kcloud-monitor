"""Ceph 분산 스토리지 조회 라우터

여러 서버의 디스크를 묶어 하나의 저장소로 쓰는 Ceph를 조회.
노드 한 대에 붙은 로컬 디스크는 노드 하위의 storage 경로에서 조회.

Ceph 메트릭 수집이 아직 연결되지 않아 모든 경로가 status="not_implemented" 반환.
"""
from fastapi import APIRouter, Depends, Request

from app.api.v2._stub import stub
from app.api.v2.deps import PaginationParams, TimeseriesParams

router = APIRouter()

CEPH_PLAN = "openkcloud_storage_ceph_plan.md"


@router.get("/clusters/{cluster}/storage/ceph/summary", summary="Ceph 요약")
async def get_ceph_summary(request: Request, cluster: str):
    """Ceph 스토리지 전체 상태를 한눈에 보는 요약 조회

    - 전체 상태(HEALTH_OK | HEALTH_WARN | HEALTH_ERR)
    - 디스크 단위(OSD) 개수와 그중 정상 동작 중인 개수
    - 전체 용량, 사용 용량, 사용률(%)
    - 저장 공간 묶음(풀) 개수
    """
    return stub(
        request,
        "Ceph 요약(health·OSD·용량·풀 수)",
        sources=("Mimir(ceph_health_status, ceph_osd_up/in, ceph_cluster_total_*)",),
        ref=f"{CEPH_PLAN} S1",
    )


@router.get("/clusters/{cluster}/storage/ceph/health", summary="Ceph health 상세")
async def get_ceph_health(request: Request, cluster: str):
    """Ceph가 왜 그 상태인지 항목별 상세 조회

    - 전체 상태(HEALTH_OK | HEALTH_WARN | HEALTH_ERR)
    - 경고나 오류를 일으킨 개별 점검 항목과 그 내용
    """
    return stub(
        request,
        "Ceph health 상태(코드+체크 상세)",
        sources=("Mimir(ceph_health_status, ceph_health_detail)",),
        ref=f"{CEPH_PLAN} S2",
    )


@router.get("/clusters/{cluster}/storage/ceph/capacity", summary="Ceph 용량")
async def get_ceph_capacity(request: Request, cluster: str):
    """Ceph 스토리지 용량 조회

    - 전체 용량, 사용 용량, 남은 용량
    - 디스크 종류(SSD, HDD 등)별로 나눈 용량 내역
    """
    return stub(
        request,
        "Ceph 용량(total/used/avail, device-class별)",
        sources=("Mimir(ceph_cluster_total_bytes, ceph_cluster_total_used_bytes)",),
        ref=f"{CEPH_PLAN} S3",
    )


@router.get("/clusters/{cluster}/storage/ceph/capacity/timeseries", summary="Ceph 용량 시계열")
async def get_ceph_capacity_timeseries(
    request: Request, cluster: str, params: TimeseriesParams = Depends()
):
    """Ceph 용량과 사용률의 시간별 변화 추이 조회

    - (시각, 사용 용량) 쌍 목록
    - (시각, 사용률(%)) 쌍 목록
    - 조회 기간과 간격은 period, start, end, step 파라미터로 지정
    """
    return stub(
        request,
        "Ceph 용량·사용률 시계열",
        sources=("Mimir(ceph_cluster_total_used_bytes range)",),
        ref=f"{CEPH_PLAN} S9",
        params=params,
    )


@router.get("/clusters/{cluster}/storage/ceph/osds", summary="OSD 목록")
async def list_ceph_osds(request: Request, cluster: str, params: PaginationParams = Depends()):
    """Ceph를 구성하는 디스크 단위(OSD) 목록 조회

    - OSD 번호, 정상 동작 여부, 클러스터 참여 여부
    - 전체 용량, 사용 용량
    - 읽기와 쓰기 응답 지연(ms)
    """
    return stub(
        request,
        "OSD 목록(상태·용량·latency)",
        sources=("Mimir(ceph_osd_up/in, ceph_osd_metadata, ceph_osd_*_latency_ms)",),
        ref=f"{CEPH_PLAN} S4",
        params=params,
    )


@router.get("/clusters/{cluster}/storage/ceph/osds/{osd_id}", summary="OSD 상세")
async def get_ceph_osd(request: Request, cluster: str, osd_id: str):
    """디스크 단위(OSD) 한 개의 상세 조회

    - OSD 번호, 이 디스크가 붙어 있는 노드와 장치 이름
    - 정상 동작 여부, 클러스터 참여 여부
    - 전체 용량, 사용 용량
    - 읽기와 쓰기 응답 지연(ms)
    """
    return stub(
        request,
        "OSD 상세",
        sources=("Mimir(ceph_osd_metadata, ceph_osd_stat_*)",),
        ref=f"{CEPH_PLAN} S5",
    )


@router.get("/clusters/{cluster}/storage/ceph/pools", summary="풀 목록")
async def list_ceph_pools(request: Request, cluster: str, params: PaginationParams = Depends()):
    """저장 공간을 용도별로 나눈 묶음(풀) 목록 조회

    - 풀 이름, 저장된 용량, 남은 용량
    - 저장된 오브젝트 개수
    - 초당 읽기, 쓰기 횟수(IOPS)
    """
    return stub(
        request,
        "Ceph 풀 목록(stored/avail/objects/IOPS)",
        sources=("Mimir(ceph_pool_metadata, ceph_pool_stored, ceph_pool_rd/wr)",),
        ref=f"{CEPH_PLAN} S6",
        params=params,
    )


@router.get("/clusters/{cluster}/storage/ceph/pools/{pool}", summary="풀 상세")
async def get_ceph_pool(request: Request, cluster: str, pool: str):
    """저장 공간 묶음(풀) 한 개의 상세 조회

    - 풀 이름, 저장된 용량, 남은 용량, 오브젝트 개수
    - 초당 읽기, 쓰기 횟수(IOPS)
    - 데이터 복제 방식과 복제 수
    """
    return stub(
        request,
        "Ceph 풀 상세",
        sources=("Mimir(ceph_pool_*)",),
        ref=f"{CEPH_PLAN} S7",
    )


@router.get("/clusters/{cluster}/storage/ceph/pgs", summary="PG 상태 요약")
async def get_ceph_pgs(request: Request, cluster: str):
    """데이터 배치 단위(Placement Group)의 상태 집계 조회

    - 전체 개수
    - active : 정상 동작 중인 개수
    - clean : 복제까지 완전히 맞춰진 개수
    - degraded : 복제본이 부족한 개수
    """
    return stub(
        request,
        "PG 상태 요약(active/clean/degraded)",
        sources=("Mimir(ceph_pg_total, ceph_pg_active, ceph_pg_clean, ceph_pg_degraded)",),
        ref=f"{CEPH_PLAN} S8",
    )


@router.get("/clusters/{cluster}/storage/summary", summary="스토리지 통합 요약")
async def get_storage_summary(request: Request, cluster: str):
    """클러스터가 쓰는 스토리지 전체를 한데 모은 요약 조회

    - 스토리지 종류별 전체 용량, 사용 용량, 사용률(%)
    - 현재는 Ceph만 반환. 다른 스토리지 추가 시 같은 응답에 함께 나옴
    """
    return stub(
        request,
        "스토리지 통합 요약(백엔드 확장 지점)",
        sources=("Mimir(ceph_*)",),
        ref=f"{CEPH_PLAN} S10",
    )
