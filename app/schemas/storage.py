"""
스토리지(Ceph) API v2 Pydantic 스키마.

공통 응답 정책: status(success | partial | error) / observed_at / warnings.
경고 코드: NO_DATA(ceph 메트릭 미수집), UPSTREAM_ERROR(Prometheus 조회 실패).

용량 단위 주의:
  cluster 단위 값(total_bytes, used_bytes)은 복제본까지 포함한 실제 디스크 소모량이고,
  풀 단위 stored_bytes 는 복제 전 논리 데이터량이다. 3벌 복제면 둘은 3배 차이가 난다.
"""
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 상태 (health)
# ---------------------------------------------------------------------------

class CephHealthCheck(BaseModel):
    """경고나 오류를 일으킨 점검 항목 하나."""

    code: str = Field(..., description="점검 항목 코드 (예: OSD_DOWN, POOL_NEAR_FULL)")
    severity: Optional[str] = Field(
        None, description="심각도 (HEALTH_WARN | HEALTH_ERR)"
    )


class CephHealthData(BaseModel):
    """Ceph 상태와 그 근거."""

    health: Optional[str] = Field(
        None, description="전체 상태 (HEALTH_OK | HEALTH_WARN | HEALTH_ERR)"
    )
    checks: list[CephHealthCheck] = Field(
        [], description="경고나 오류를 일으킨 점검 항목 목록. 정상이면 빈 목록"
    )


class CephHealthResponse(BaseModel):
    status: str
    data: Optional[CephHealthData] = None
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# 용량 (capacity)
# ---------------------------------------------------------------------------

class CephDeviceClassCapacity(BaseModel):
    """디스크 종류별 용량 내역."""

    device_class: str = Field(..., description="디스크 종류 (ssd | hdd | nvme)")
    total_bytes: float = Field(0, description="이 종류의 전체 용량 (bytes)")
    used_bytes: float = Field(0, description="이 종류의 사용 용량 (bytes)")
    usage_percent: Optional[float] = Field(None, description="이 종류의 사용률 (%)")
    osd_count: int = Field(0, description="이 종류에 속한 OSD 개수")


class CephCapacityData(BaseModel):
    """Ceph 전체 용량. 복제본을 포함한 실제 디스크 소모 기준."""

    total_bytes: Optional[float] = Field(None, description="전체 용량 (bytes)")
    used_bytes: Optional[float] = Field(None, description="사용 용량 (bytes)")
    available_bytes: Optional[float] = Field(None, description="남은 용량 (bytes)")
    usage_percent: Optional[float] = Field(None, description="사용률 (%)")
    by_device_class: list[CephDeviceClassCapacity] = Field(
        [], description="디스크 종류별로 나눈 용량 내역"
    )


class CephCapacityResponse(BaseModel):
    status: str
    data: Optional[CephCapacityData] = None
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class CephCapacitySeries(BaseModel):
    """용량 시계열의 단일 시리즈."""

    name: str = Field(..., description="시리즈 종류 (used_bytes | usage_percent)")
    values: list[tuple[str, str]] = Field(
        ..., description="(ISO 8601 UTC 시각, 값 문자열) 쌍 목록"
    )


class CephCapacityTimeseriesResponse(BaseModel):
    status: str
    series: list[CephCapacitySeries] = []
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# OSD (디스크 단위)
# ---------------------------------------------------------------------------

class CephOSDItem(BaseModel):
    """Ceph를 구성하는 디스크 단위(OSD) 하나."""

    osd_id: str = Field(..., description="OSD 이름 (예: osd.7)")
    up: Optional[bool] = Field(
        None, description="데몬이 살아서 통신 중인지 여부"
    )
    in_cluster: Optional[bool] = Field(
        None, description="데이터 배치 대상에 포함되는지 여부. Ceph 표기로는 in"
    )
    hostname: Optional[str] = Field(None, description="이 디스크가 붙어 있는 노드 이름")
    device_class: Optional[str] = Field(None, description="디스크 종류 (ssd | hdd | nvme)")
    device: Optional[str] = Field(None, description="장치 이름 (예: sdb)")
    total_bytes: Optional[float] = Field(None, description="전체 용량 (bytes)")
    used_bytes: Optional[float] = Field(None, description="사용 용량 (bytes)")
    usage_percent: Optional[float] = Field(None, description="사용률 (%)")
    apply_latency_ms: Optional[float] = Field(None, description="쓰기 반영 지연 (ms)")
    commit_latency_ms: Optional[float] = Field(None, description="쓰기 확정 지연 (ms)")


class CephOSDSummaryData(BaseModel):
    """OSD 상태 집계."""

    total: int = Field(0, description="전체 OSD 개수")
    up: int = Field(0, description="살아서 통신 중인 개수")
    in_cluster: int = Field(0, description="데이터 배치 대상에 포함된 개수")
    down: int = Field(0, description="통신이 끊긴 개수")
    out: int = Field(0, description="데이터 배치 대상에서 빠진 개수")


class CephOSDListResponse(BaseModel):
    status: str
    data: list[CephOSDItem] = []
    total: int = Field(0, description="검색 조건에 맞는 전체 OSD 개수")
    summary: CephOSDSummaryData = Field(
        default_factory=CephOSDSummaryData,
        description="전체 OSD 집계. 검색과 페이지 범위에 영향받지 않는 전체 기준",
    )
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class CephOSDDetailResponse(BaseModel):
    status: str
    data: Optional[CephOSDItem] = None
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# 풀 (용도별 저장 공간 묶음)
# ---------------------------------------------------------------------------

class CephPoolItem(BaseModel):
    """저장 공간을 용도별로 나눈 묶음(풀) 하나."""

    name: str = Field(..., description="풀 이름 (예: volumes, images)")
    pool_id: Optional[str] = Field(None, description="풀 번호")
    stored_bytes: Optional[float] = Field(
        None, description="저장된 논리 데이터량 (bytes). 복제본을 세지 않은 값"
    )
    available_bytes: Optional[float] = Field(
        None, description="이 풀이 더 쓸 수 있는 용량 (bytes). 풀끼리 전체 용량을 나눠 쓰므로 합계는 전체 용량과 다름"
    )
    objects: Optional[float] = Field(None, description="저장된 오브젝트 개수")
    read_ops_per_sec: Optional[float] = Field(None, description="초당 읽기 횟수")
    write_ops_per_sec: Optional[float] = Field(None, description="초당 쓰기 횟수")


class CephPoolDetail(CephPoolItem):
    """풀 하나의 상세. 목록 항목에 복제 설정을 더한 형태."""

    replication_type: Optional[str] = Field(
        None, description="데이터 보호 방식 (replicated | erasure)"
    )
    replication_size: Optional[int] = Field(
        None, description="복제본 개수. erasure 방식이면 데이터 조각과 패리티 조각의 합"
    )


class CephPoolListResponse(BaseModel):
    status: str
    data: list[CephPoolItem] = []
    total: int = Field(0, description="검색 조건에 맞는 전체 풀 개수")
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class CephPoolDetailResponse(BaseModel):
    status: str
    data: Optional[CephPoolDetail] = None
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# PG (데이터 배치 단위)
# ---------------------------------------------------------------------------

class CephPGData(BaseModel):
    """데이터 배치 단위(Placement Group) 상태 집계."""

    total: Optional[float] = Field(None, description="전체 개수")
    active: Optional[float] = Field(None, description="읽기와 쓰기를 처리할 수 있는 개수")
    clean: Optional[float] = Field(None, description="복제본까지 정확히 맞춰진 개수")
    degraded: Optional[float] = Field(
        None, description="복제본이 부족한 개수. 0보다 크면 디스크가 하나 더 빠질 때 데이터 손실 위험"
    )


class CephPGResponse(BaseModel):
    status: str
    data: Optional[CephPGData] = None
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# 전체 요약
# ---------------------------------------------------------------------------

class CephSummaryData(BaseModel):
    """Ceph 전체 상태 요약."""

    health: Optional[str] = Field(
        None, description="전체 상태 (HEALTH_OK | HEALTH_WARN | HEALTH_ERR)"
    )
    osd_total: int = Field(0, description="전체 OSD 개수")
    osd_up: int = Field(0, description="살아서 통신 중인 OSD 개수")
    osd_in: int = Field(0, description="데이터 배치 대상에 포함된 OSD 개수")
    total_bytes: Optional[float] = Field(None, description="전체 용량 (bytes)")
    used_bytes: Optional[float] = Field(None, description="사용 용량 (bytes)")
    usage_percent: Optional[float] = Field(None, description="사용률 (%)")
    pool_count: int = Field(0, description="저장 공간 묶음(풀) 개수")


class CephSummaryResponse(BaseModel):
    status: str
    data: Optional[CephSummaryData] = None
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []
