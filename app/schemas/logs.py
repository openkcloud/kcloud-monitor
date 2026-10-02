"""
로그 API v2 Pydantic 스키마 (OPT.003 §3-1, §3-2).

공통 응답 정책(docs/API_GUIDE.md):
  - observed_at, warnings[] 동일
  - 로그 응답은 data[] + pagination 구조 (sample_api §11)
"""
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 로그 엔트리
# ---------------------------------------------------------------------------

class LogEntry(BaseModel):
    """로그 한 줄."""

    timestamp: str = Field(..., description="ISO 8601 타임스탬프")
    log_level: str = Field("info", description="error | warning | info | debug")
    message: str = Field(..., description="로그 원문")
    labels: dict[str, str] = Field(default_factory=dict, description="Loki 스트림 라벨")
    detected_fields: dict[str, Any] = Field(
        default_factory=dict, description="자동 추출 구조화 필드 (XID, OOM 등)"
    )
    trace_id: str = Field("", description="OTel trace ID (미계측 시 빈 문자열)")
    span_id: str = Field("", description="OTel span ID (미계측 시 빈 문자열)")


class LogPagination(BaseModel):
    """페이지 정보. Loki는 시각을 기준으로 넘기므로 offset은 항상 0이고 next_cursor로 이어 받음."""

    total: int = Field(..., description="이번 페이지에 담긴 로그 수. 전체 건수가 아님")
    limit: int
    offset: int = 0
    has_next: bool = Field(False, description="다음 페이지 존재 여부")
    next_cursor: Optional[str] = Field(
        None, description="다음 페이지를 받을 때 cursor 파라미터로 그대로 넘기는 값. 마지막 페이지면 null",
    )


# ---------------------------------------------------------------------------
# 응답 모델
# ---------------------------------------------------------------------------

class LogSearchResponse(BaseModel):
    """GET /logs/search, /logs/clusters/{c}/search 등 범용 로그 응답."""

    status: str = "success"
    observed_at: str = Field(default_factory=_now)
    data: list[LogEntry] = []
    pagination: LogPagination
    warnings: list[str] = []


class PodLogResponse(BaseModel):
    """GET /logs/clusters/{c}/pods/{ns}/{pod}/logs 응답."""

    status: str = "success"
    observed_at: str = Field(default_factory=_now)
    cluster: str
    namespace: str
    pod: str
    data: list[LogEntry] = []
    pagination: LogPagination
    warnings: list[str] = []


class AcceleratorLogResponse(BaseModel):
    """GET /logs/clusters/{c}/accelerators/{id}/logs 응답."""

    status: str = "success"
    observed_at: str = Field(default_factory=_now)
    cluster: str
    accelerator_id: str
    data: list[LogEntry] = []
    pagination: LogPagination
    warnings: list[str] = []


class NodeLogResponse(BaseModel):
    """GET /logs/clusters/{c}/nodes/{n}/logs 응답."""

    status: str = "success"
    observed_at: str = Field(default_factory=_now)
    cluster: str
    node: str
    data: list[LogEntry] = []
    pagination: LogPagination
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# 라벨 / 볼륨
# ---------------------------------------------------------------------------

class LabelListResponse(BaseModel):
    """GET /logs/labels 응답."""

    status: str = "success"
    observed_at: str = Field(default_factory=_now)
    data: list[str] = []
    warnings: list[str] = Field(default_factory=list, description="LOKI_UNAVAILABLE: Loki 조회 실패. 빈 목록이 실제로 비어서인지 장애인지 구분")


class LabelValuesResponse(BaseModel):
    """GET /logs/label-values 응답."""

    status: str = "success"
    observed_at: str = Field(default_factory=_now)
    label: str
    data: list[str] = []
    warnings: list[str] = Field(default_factory=list, description="LOKI_UNAVAILABLE: Loki 조회 실패. 빈 목록이 실제로 비어서인지 장애인지 구분")


class VolumeEntry(BaseModel):
    """로그 볼륨 단일 항목."""

    labels: dict[str, str] = Field(default_factory=dict)
    volume: str = Field("0", description="바이트 수 (문자열)")


class VolumeResponse(BaseModel):
    """GET /logs/volume 응답."""

    status: str = "success"
    observed_at: str = Field(default_factory=_now)
    data: list[VolumeEntry] = []
    warnings: list[str] = Field(default_factory=list, description="LOKI_UNAVAILABLE: Loki 조회 실패. 빈 목록이 실제로 비어서인지 장애인지 구분")
