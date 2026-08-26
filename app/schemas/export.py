"""
KCloud Monitor v2 — Export 도메인 스키마.

CSV/JSON 내보내기 응답 모델과 허용 메트릭 목록.
CSV 응답은 `fastapi.responses.Response`로 파일 스트림을 직접 반환하므로
FastAPI response_model을 적용하지 않는다(json 포맷만 아래 모델을 사용).

허용 메트릭 목록(EXPORT_METRIC_ALLOWLIST)은 app.schemas.monitoring.METRIC_ALLOWLIST와
별도로 둔다 — export API는 원본 Prometheus 메트릭 이름을 그대로 파라미터로 받으므로
(예: DCGM_FI_DEV_POWER_USAGE), 별칭이 아닌 실제 메트릭명을 키로 사용해 임의 PromQL
주입을 차단한다.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 허용 메트릭 목록 (EXPORT_METRIC_ALLOWLIST) — 키/값 모두 원본 메트릭명 또는 안전한 PromQL
# ---------------------------------------------------------------------------
EXPORT_METRIC_ALLOWLIST: dict[str, str] = {
    # NVIDIA (L40S)
    "DCGM_FI_DEV_POWER_USAGE": "DCGM_FI_DEV_POWER_USAGE",
    "DCGM_FI_DEV_GPU_TEMP": "DCGM_FI_DEV_GPU_TEMP",
    "DCGM_FI_DEV_GPU_UTIL": "DCGM_FI_DEV_GPU_UTIL",
    "DCGM_FI_PROF_GR_ENGINE_ACTIVE": "DCGM_FI_PROF_GR_ENGINE_ACTIVE",
    "DCGM_FI_DEV_FB_USED": "DCGM_FI_DEV_FB_USED",
    "DCGM_FI_DEV_FB_FREE": "DCGM_FI_DEV_FB_FREE",
    "DCGM_FI_DEV_XID_ERRORS": "DCGM_FI_DEV_XID_ERRORS",
    # Furiosa
    "furiosa_npu_hw_power": "furiosa_npu_hw_power",
    "furiosa_npu_hw_temperature": "furiosa_npu_hw_temperature",
    "furiosa_npu_core_utilization": "furiosa_npu_core_utilization",
    "furiosa_npu_dram_usage": "furiosa_npu_dram_usage",
    "furiosa_npu_alive": "furiosa_npu_alive",
    # Rebellions — 콜론 포함 메트릭명은 PromQL 파서가 원형을 거부하므로 __name__ 매칭 사용
    "RBLN_DEVICE_STATUS:CARD_POWER": '{__name__="RBLN_DEVICE_STATUS:CARD_POWER"}',
    "RBLN_DEVICE_STATUS:UTILIZATION": '{__name__="RBLN_DEVICE_STATUS:UTILIZATION"}',
    "RBLN_DEVICE_STATUS:DRAM_USED": '{__name__="RBLN_DEVICE_STATUS:DRAM_USED"}',
    "RBLN_DEVICE_STATUS:HEALTH": '{__name__="RBLN_DEVICE_STATUS:HEALTH"}',
    # 전력 3계층 공통
    "ipmi_dcmi_power_consumption_watts": "ipmi_dcmi_power_consumption_watts",
    "kepler_node_cpu_watts": "kepler_node_cpu_watts",
}


_KST = timezone(timedelta(hours=9))


def _now() -> str:
    return datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# /export/power
# ---------------------------------------------------------------------------

class PowerExportRow(BaseModel):
    """전력 내보내기 단일 행."""

    timestamp: str
    node: str
    layer: str
    watts: Optional[float]


class PowerExportData(BaseModel):
    rows: list[PowerExportRow]


class PowerExportResponse(BaseModel):
    """GET /export/power?format=json 응답."""

    status: str
    data: Optional[PowerExportData]
    observed_at: str = Field(default_factory=_now)
    is_stale: bool = False
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# /export/metrics
# ---------------------------------------------------------------------------

class MetricExportRow(BaseModel):
    """메트릭 내보내기 단일 행."""

    timestamp: str
    labels: dict[str, str]
    value: Optional[float]


class MetricExportData(BaseModel):
    metric: str
    rows: list[MetricExportRow]


class MetricExportResponse(BaseModel):
    """GET /export/metrics?format=json 응답."""

    status: str
    data: Optional[MetricExportData]
    observed_at: str = Field(default_factory=_now)
    is_stale: bool = False
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# /export/report
# ---------------------------------------------------------------------------

class ReportExportResponse(BaseModel):
    """GET /export/report 응답 — pdf 생성 의존성 미도입으로 항상 NOT_CONFIGURED."""

    status: str
    data: Optional[Any] = None
    report_type: str
    observed_at: str = Field(default_factory=_now)
    is_stale: bool = False
    warnings: list[str] = []
