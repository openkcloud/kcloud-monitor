"""Ceph 분산 스토리지 조회 라우터

여러 서버의 디스크를 묶어 하나의 저장소로 쓰는 Ceph를 조회.
노드 한 대에 붙은 로컬 디스크는 노드 하위의 storage 경로에서 조회.

Ceph는 관리 클러스터에만 있고 메트릭에 cluster 라벨이 붙지 않아, 경로에 클러스터를 받지 않는다.
메트릭이 수집되지 않으면 status="partial" 과 NO_DATA 경고를 반환한다.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.api.v2.deps import PaginationParams, TimeseriesParams
from app.schemas.storage import (
    CephCapacityData,
    CephCapacityResponse,
    CephCapacitySeries,
    CephCapacityTimeseriesResponse,
    CephDeviceClassCapacity,
    CephHealthCheck,
    CephHealthData,
    CephHealthResponse,
    CephOSDDetailResponse,
    CephOSDItem,
    CephOSDListResponse,
    CephOSDSummaryData,
    CephPGData,
    CephPGResponse,
    CephPoolDetail,
    CephPoolDetailResponse,
    CephPoolItem,
    CephPoolListResponse,
    CephSummaryData,
    CephSummaryResponse,
)
from app.services import ceph

router = APIRouter()


# ---------------------------------------------------------------------------
# 공통 계산
# ---------------------------------------------------------------------------

def _percent(used: Optional[float], total: Optional[float]) -> Optional[float]:
    """사용률(%). 전체 용량이 없거나 0이면 None."""
    if used is None or not total:
        return None
    return round(used / total * 100, 2)


def _osd_number(osd_id: str) -> tuple:
    """osd.10 이 osd.2 뒤에 오도록 숫자로 정렬하기 위한 키."""
    suffix = osd_id.split(".")[-1]
    return (0, int(suffix)) if suffix.isdigit() else (1, 0)


def _osd_key(osd_id: str) -> str:
    """경로로 받은 7 과 osd.7 을 같은 값으로 취급."""
    return osd_id if osd_id.startswith("osd.") else f"osd.{osd_id}"


def _sort_value(item, field: str):
    """정렬 키. 값이 없는 항목은 뒤로 보낸다."""
    value = getattr(item, field, None)
    return (value is None, value if isinstance(value, (int, float)) else str(value or ""))


def _page(items: list, params: PaginationParams, search_fields: tuple[str, ...]):
    """검색·정렬·페이지 적용. (현재 페이지, 검색 후 전체 개수) 반환."""
    if params.search:
        keyword = params.search.lower()
        items = [
            i for i in items
            if any(keyword in str(getattr(i, f, "") or "").lower() for f in search_fields)
        ]
    if params.sort_by:
        items = sorted(items, key=lambda i: _sort_value(i, params.sort_by),
                       reverse=params.sort_order == "desc")
    return items[params.offset:params.offset + params.limit], len(items)


def _osd_items(snap: ceph.CephSnapshot) -> list[CephOSDItem]:
    """OSD 값 메트릭과 metadata 라벨을 ceph_daemon 기준으로 이어 붙인다."""
    up = snap.by_label("ceph_osd_up", "ceph_daemon")
    joined = snap.by_label("ceph_osd_in", "ceph_daemon")
    total = snap.by_label("ceph_osd_stat_bytes", "ceph_daemon")
    used = snap.by_label("ceph_osd_stat_bytes_used", "ceph_daemon")
    apply_ms = snap.by_label("ceph_osd_apply_latency_ms", "ceph_daemon")
    commit_ms = snap.by_label("ceph_osd_commit_latency_ms", "ceph_daemon")
    meta = snap.labels_by("ceph_osd_metadata", "ceph_daemon")

    items = []
    for osd_id in sorted(set(up) | set(joined) | set(total) | set(meta), key=_osd_number):
        labels = meta.get(osd_id, {})
        items.append(CephOSDItem(
            osd_id=osd_id,
            up=bool(up[osd_id]) if osd_id in up else None,
            in_cluster=bool(joined[osd_id]) if osd_id in joined else None,
            hostname=labels.get("hostname"),
            device_class=labels.get("device_class"),
            device=labels.get("devices") or labels.get("device"),
            total_bytes=total.get(osd_id),
            used_bytes=used.get(osd_id),
            usage_percent=_percent(used.get(osd_id), total.get(osd_id)),
            apply_latency_ms=apply_ms.get(osd_id),
            commit_latency_ms=commit_ms.get(osd_id),
        ))
    return items


def _osd_summary(items: list[CephOSDItem]) -> CephOSDSummaryData:
    """OSD 상태 집계. up/down 과 in/out 은 서로 다른 축이라 따로 센다."""
    return CephOSDSummaryData(
        total=len(items),
        up=sum(1 for i in items if i.up),
        in_cluster=sum(1 for i in items if i.in_cluster),
        down=sum(1 for i in items if i.up is False),
        out=sum(1 for i in items if i.in_cluster is False),
    )


async def _pool_items(snap: ceph.CephSnapshot) -> list[CephPoolItem]:
    """풀 값 메트릭과 metadata 라벨을 pool_id 기준으로 이어 붙인다."""
    stored = snap.by_label("ceph_pool_stored", "pool_id")
    avail = snap.by_label("ceph_pool_max_avail", "pool_id")
    objects = snap.by_label("ceph_pool_objects", "pool_id")
    meta = snap.labels_by("ceph_pool_metadata", "pool_id")
    iops = await ceph.pool_iops()

    items = []
    for pool_id in sorted(set(stored) | set(meta)):
        labels = meta.get(pool_id, {})
        items.append(CephPoolItem(
            name=labels.get("name") or pool_id,
            pool_id=pool_id,
            stored_bytes=stored.get(pool_id),
            available_bytes=avail.get(pool_id),
            objects=objects.get(pool_id),
            read_ops_per_sec=iops["read"].get(pool_id),
            write_ops_per_sec=iops["write"].get(pool_id),
        ))
    return items


# ---------------------------------------------------------------------------
# 라우트
# ---------------------------------------------------------------------------

@router.get("/storage/ceph/summary", summary="Ceph 요약", response_model=CephSummaryResponse)
async def get_ceph_summary():
    """Ceph 스토리지 전체 상태를 한눈에 보는 요약 조회

    - 전체 상태(HEALTH_OK | HEALTH_WARN | HEALTH_ERR)
    - 디스크 단위(OSD) 개수, 살아있는 개수, 데이터 배치 대상 개수
    - 전체 용량(bytes), 사용 용량(bytes), 사용률(%)
    - 저장 공간 묶음(풀) 개수
    - 메트릭 미수집 시 NO_DATA 경고 반환
    """
    snap = await ceph.snapshot()
    if snap.empty:
        return CephSummaryResponse(status="partial", warnings=["NO_DATA"])

    osds = _osd_items(snap)
    total = snap.scalar(ceph.CAPACITY_TOTAL)
    used = snap.scalar(ceph.CAPACITY_USED)
    counts = _osd_summary(osds)
    return CephSummaryResponse(
        status="success",
        data=CephSummaryData(
            health=ceph.health_code(snap.scalar("ceph_health_status")),
            osd_total=counts.total,
            osd_up=counts.up,
            osd_in=counts.in_cluster,
            total_bytes=total,
            used_bytes=used,
            usage_percent=_percent(used, total),
            pool_count=len(snap.series("ceph_pool_metadata")) or len(
                snap.by_label("ceph_pool_stored", "pool_id")
            ),
        ),
    )


@router.get("/storage/ceph/health", summary="Ceph health 상세", response_model=CephHealthResponse)
async def get_ceph_health():
    """Ceph가 왜 그 상태인지 항목별 상세 조회

    - 전체 상태(HEALTH_OK | HEALTH_WARN | HEALTH_ERR)
    - 경고나 오류를 일으킨 점검 항목 코드와 심각도
    - 메트릭 미수집 시 NO_DATA 경고 반환
    """
    snap = await ceph.snapshot()
    if snap.empty:
        return CephHealthResponse(status="partial", warnings=["NO_DATA"])

    checks = [
        CephHealthCheck(code=labels.get("name", ""), severity=labels.get("severity"))
        for labels, value in snap.series("ceph_health_detail")
        if value > 0 and labels.get("name")
    ]
    return CephHealthResponse(
        status="success",
        data=CephHealthData(
            health=ceph.health_code(snap.scalar("ceph_health_status")),
            checks=checks,
        ),
    )


@router.get("/storage/ceph/capacity", summary="Ceph 용량", response_model=CephCapacityResponse)
async def get_ceph_capacity():
    """Ceph 스토리지 용량 조회

    - 전체 용량(bytes), 사용 용량(bytes), 남은 용량(bytes), 사용률(%)
    - 디스크 종류(ssd | hdd | nvme)별로 나눈 용량과 OSD 개수
    - 복제본까지 포함한 실제 디스크 소모 기준
    - 메트릭 미수집 시 NO_DATA 경고 반환
    """
    snap = await ceph.snapshot()
    if snap.empty:
        return CephCapacityResponse(status="partial", warnings=["NO_DATA"])

    total = snap.scalar(ceph.CAPACITY_TOTAL)
    used = snap.scalar(ceph.CAPACITY_USED)

    by_class: dict[str, dict] = {}
    for osd in _osd_items(snap):
        if not osd.device_class:
            continue
        bucket = by_class.setdefault(
            osd.device_class, {"total": 0.0, "used": 0.0, "count": 0}
        )
        bucket["total"] += osd.total_bytes or 0.0
        bucket["used"] += osd.used_bytes or 0.0
        bucket["count"] += 1

    return CephCapacityResponse(
        status="success",
        data=CephCapacityData(
            total_bytes=total,
            used_bytes=used,
            available_bytes=(total - used) if total is not None and used is not None else None,
            usage_percent=_percent(used, total),
            by_device_class=[
                CephDeviceClassCapacity(
                    device_class=name,
                    total_bytes=v["total"],
                    used_bytes=v["used"],
                    usage_percent=_percent(v["used"], v["total"]),
                    osd_count=v["count"],
                )
                for name, v in sorted(by_class.items())
            ],
        ),
    )


@router.get("/storage/ceph/capacity/timeseries", summary="Ceph 용량 시계열",
            response_model=CephCapacityTimeseriesResponse)
async def get_ceph_capacity_timeseries(params: TimeseriesParams = Depends()):
    """Ceph 용량과 사용률의 시간별 변화 추이 조회

    - used_bytes : (시각, 사용 용량) 쌍 목록
    - usage_percent : (시각, 사용률) 쌍 목록
    - 조회 기간과 간격은 period, start, end, step 파라미터로 지정
    - 메트릭 미수집 시 NO_DATA 경고 반환
    """
    now = datetime.now(timezone.utc)
    points = await ceph.capacity_range(params.start_iso(now), params.end_iso(now), params.step)
    if not points:
        return CephCapacityTimeseriesResponse(status="partial", warnings=["NO_DATA"])

    def _iso(at: float) -> str:
        return datetime.fromtimestamp(at, tz=timezone.utc).isoformat()

    return CephCapacityTimeseriesResponse(
        status="success",
        series=[
            CephCapacitySeries(
                name="used_bytes",
                values=[(_iso(at), str(used)) for at, used, _ in points],
            ),
            CephCapacitySeries(
                name="usage_percent",
                values=[(_iso(at), str(pct)) for at, _, pct in points if pct is not None],
            ),
        ],
    )


@router.get("/storage/ceph/osds", summary="OSD 목록", response_model=CephOSDListResponse)
async def list_ceph_osds(params: PaginationParams = Depends()):
    """Ceph를 구성하는 디스크 단위(OSD) 목록 조회

    - OSD 이름, 데몬 동작 여부, 데이터 배치 대상 포함 여부
    - 붙어 있는 노드 이름, 디스크 종류(ssd | hdd | nvme), 장치 이름
    - 전체 용량(bytes), 사용 용량(bytes), 사용률(%)
    - 쓰기 반영 지연(ms), 쓰기 확정 지연(ms)
    - summary는 검색과 페이지 범위에 영향받지 않는 전체 기준
    - 메트릭 미수집 시 NO_DATA 경고 반환

    검색은 OSD 이름과 노드 이름에 적용.
    """
    snap = await ceph.snapshot()
    if snap.empty:
        return CephOSDListResponse(status="partial", warnings=["NO_DATA"])

    items = _osd_items(snap)
    page, total = _page(items, params, ("osd_id", "hostname"))
    return CephOSDListResponse(
        status="success", data=page, total=total, summary=_osd_summary(items)
    )


@router.get("/storage/ceph/osds/{osd_id}", summary="OSD 상세",
            response_model=CephOSDDetailResponse)
async def get_ceph_osd(osd_id: str):
    """디스크 단위(OSD) 한 개의 상세 조회

    - OSD 이름, 데몬 동작 여부, 데이터 배치 대상 포함 여부
    - 붙어 있는 노드 이름, 디스크 종류(ssd | hdd | nvme), 장치 이름
    - 전체 용량(bytes), 사용 용량(bytes), 사용률(%)
    - 쓰기 반영 지연(ms), 쓰기 확정 지연(ms)
    - 메트릭 미수집 시 NO_DATA 경고 반환

    osd_id는 7 과 osd.7 둘 다 허용. 없는 OSD는 404.
    """
    snap = await ceph.snapshot()
    if snap.empty:
        return CephOSDDetailResponse(status="partial", warnings=["NO_DATA"])

    wanted = _osd_key(osd_id)
    for item in _osd_items(snap):
        if item.osd_id == wanted:
            return CephOSDDetailResponse(status="success", data=item)
    raise HTTPException(status_code=404, detail=f"알 수 없는 OSD: {osd_id}")


@router.get("/storage/ceph/pools", summary="풀 목록", response_model=CephPoolListResponse)
async def list_ceph_pools(params: PaginationParams = Depends()):
    """저장 공간을 용도별로 나눈 묶음(풀) 목록 조회

    - 풀 이름, 풀 번호
    - 저장된 논리 데이터량(bytes), 더 쓸 수 있는 용량(bytes), 오브젝트 개수
    - 초당 읽기 횟수, 초당 쓰기 횟수
    - 메트릭 미수집 시 NO_DATA 경고 반환

    검색은 풀 이름에 적용.
    """
    snap = await ceph.snapshot()
    if snap.empty:
        return CephPoolListResponse(status="partial", warnings=["NO_DATA"])

    page, total = _page(await _pool_items(snap), params, ("name", "pool_id"))
    return CephPoolListResponse(status="success", data=page, total=total)


@router.get("/storage/ceph/pools/{pool}", summary="풀 상세",
            response_model=CephPoolDetailResponse)
async def get_ceph_pool(pool: str):
    """저장 공간 묶음(풀) 한 개의 상세 조회

    - 풀 이름, 풀 번호
    - 저장된 논리 데이터량(bytes), 더 쓸 수 있는 용량(bytes), 오브젝트 개수
    - 초당 읽기 횟수, 초당 쓰기 횟수
    - 데이터 보호 방식(replicated | erasure), 복제본 개수
    - 메트릭 미수집 시 NO_DATA 경고 반환

    pool은 풀 이름과 풀 번호 둘 다 허용. 없는 풀은 404.
    """
    snap = await ceph.snapshot()
    if snap.empty:
        return CephPoolDetailResponse(status="partial", warnings=["NO_DATA"])

    meta = snap.labels_by("ceph_pool_metadata", "pool_id")
    for item in await _pool_items(snap):
        if pool not in (item.name, item.pool_id):
            continue
        labels = meta.get(item.pool_id or "", {})
        size = labels.get("size")
        return CephPoolDetailResponse(
            status="success",
            data=CephPoolDetail(
                **item.model_dump(),
                replication_type=labels.get("type"),
                replication_size=int(size) if size and size.isdigit() else None,
            ),
        )
    raise HTTPException(status_code=404, detail=f"알 수 없는 풀: {pool}")


@router.get("/storage/ceph/pgs", summary="PG 상태 요약", response_model=CephPGResponse)
async def get_ceph_pgs():
    """데이터 배치 단위(Placement Group)의 상태 집계 조회

    - 전체 개수
    - active : 읽기와 쓰기를 처리할 수 있는 개수
    - clean : 복제본까지 정확히 맞춰진 개수
    - degraded : 복제본이 부족한 개수
    - 메트릭 미수집 시 NO_DATA 경고 반환
    """
    snap = await ceph.snapshot()
    if snap.empty:
        return CephPGResponse(status="partial", warnings=["NO_DATA"])

    return CephPGResponse(
        status="success",
        data=CephPGData(
            total=snap.scalar("ceph_pg_total"),
            active=snap.scalar("ceph_pg_active"),
            clean=snap.scalar("ceph_pg_clean"),
            degraded=snap.scalar("ceph_pg_degraded"),
        ),
    )
