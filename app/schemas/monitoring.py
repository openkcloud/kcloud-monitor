"""
모니터링 API v2 Pydantic 스키마 및 메트릭 허용 목록.

공통 응답 정책(docs/API_GUIDE.md §공통-응답-정책):
  - status: "success" | "partial" | "error"
  - observed_at: ISO 8601 수집 시각
  - warnings[]: STALE_DATA, PARTIAL_SOURCE, ESTIMATED_POWER 등 (정상 시 빈 목록)
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 메트릭 허용 목록 (METRIC_ALLOWLIST)
# ---------------------------------------------------------------------------
# 헬스 방향 주의:
#   furiosa_alive    → 1 = 정상
#   rebellions_health → 0 = 정상 (방향 반전!)
#   gpu_xid_errors   → 0 = 정상 (오류 카운트)
# 메모리 단위 주의:
#   L40S GPU → MiB
#   Furiosa / Rebellions NPU → bytes
# ---------------------------------------------------------------------------
METRIC_ALLOWLIST: dict[str, str] = {
    # 온도 (Temperature)
    "gpu_temperature": 'DCGM_FI_DEV_GPU_TEMP{cluster="l40s"}',
    "furiosa_temperature": 'furiosa_npu_hw_temperature{label="peak",cluster="k8s-furiosa-rngd"}',
    # 사용률 (Utilization)
    "gpu_utilization": 'DCGM_FI_DEV_GPU_UTIL{cluster="l40s"}',
    "gpu_utilization_prof": 'DCGM_FI_PROF_GR_ENGINE_ACTIVE{cluster="l40s"} * 100',
    "furiosa_utilization": 'furiosa_npu_core_utilization{cluster="k8s-furiosa-rngd"}',
    "rebellions_utilization": 'RBLN_DEVICE_STATUS:UTILIZATION{cluster="rebellions"}',
    # 메모리 (Memory) — 단위 주의: L40S=MiB, NPU=bytes
    "gpu_memory_used_mib": 'DCGM_FI_DEV_FB_USED{cluster="l40s"}',
    "gpu_memory_free_mib": 'DCGM_FI_DEV_FB_FREE{cluster="l40s"}',
    "furiosa_memory_used_bytes": 'furiosa_npu_dram_usage{cluster="k8s-furiosa-rngd"}',
    "rebellions_memory_used_bytes": 'RBLN_DEVICE_STATUS:DRAM_USED{cluster="rebellions"}',
    # 헬스 (Health) — 방향 주의!
    "furiosa_alive": 'furiosa_npu_alive{cluster="k8s-furiosa-rngd"}',          # 1=정상
    "rebellions_health": 'RBLN_DEVICE_STATUS:HEALTH{cluster="rebellions"}',    # 0=정상!
    "gpu_xid_errors": 'changes(DCGM_FI_DEV_XID_ERRORS{cluster="l40s"}[10m])', # 0=정상
}


# ---------------------------------------------------------------------------
# 공통 헬퍼
# ---------------------------------------------------------------------------

_KST = timezone(timedelta(hours=9))


def _now() -> str:
    return datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 모델
# ---------------------------------------------------------------------------

class ClusterCounts(BaseModel):
    """클러스터 구분별 개수 (관리/서비스). [docs/API_RESTRUCTURE_PLAN.md §4.7]"""

    total: int = Field(..., description="전체 클러스터 수 (관리 + 서비스)")
    management: int = Field(..., description="관리 클러스터 수 (물리+OpenStack, 현재 1: mgmt)")
    service: int = Field(..., description="서비스 클러스터 수 (VM 위 K8s, parent=관리)")


class NodeCounts(BaseModel):
    """노드 구분별 개수 (물리/가상) 및 헬스. [docs/API_RESTRUCTURE_PLAN.md §4.7]"""

    total: int = Field(..., description="전체 노드 수 (물리 + 가상)")
    physical: int = Field(..., description="물리 노드 수 — 관리 클러스터 kube_node_info 기준")
    virtual: int = Field(..., description="가상(VM) 노드 수 — 서비스 클러스터별 node_uname_info 합계")
    healthy: int = Field(..., description="정상 노드 수 — 물리는 kube_node_status_condition Ready 기준, 서비스 VM은 근사치 포함")
    unhealthy: int = Field(..., description="비정상 노드 수 — 물리 노드 중 Ready가 아닌 수")


class OverviewData(BaseModel):
    """전체 시스템 KPI 데이터."""

    clusters: ClusterCounts
    nodes: NodeCounts
    accelerator_count: int
    healthy_count: int
    avg_temperature: dict[str, Optional[float]] = Field(
        ..., description="클러스터 라벨값(l40s/k8s-furiosa-rngd/rebellions)별 평균 온도 (섭씨 °C)"
    )


class OverviewResponse(BaseModel):
    """GET /monitoring/overview 응답."""

    status: str
    data: OverviewData
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class MetricSample(BaseModel):
    """Prometheus instant/range 쿼리의 단일 시계열 샘플.

    instant 쿼리: value 사용 (timestamp, value 문자열 튜플).
    range 쿼리:   values 사용 (튜플 목록).
    """

    metric: dict[str, str]
    value: Optional[tuple[float, str]] = None
    values: Optional[list[tuple[str, str]]] = Field(
        None, description="(ISO 8601 UTC 타임스탬프, 값 문자열) 튜플 목록"
    )


class MetricsQueryResponse(BaseModel):
    """GET /monitoring/metrics/query 응답 (instant 쿼리)."""

    status: str
    metric: str
    results: list[MetricSample]
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class TimeseriesResponse(BaseModel):
    """GET /monitoring/metrics/timeseries 응답 (range 쿼리)."""

    status: str
    metric: str
    series: list[MetricSample]
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class TemperatureSeriesItem(BaseModel):
    """온도 시계열의 단일 장치 시리즈."""

    vendor: str
    cluster: str
    metric_labels: dict[str, str]
    values: list[tuple[str, str]] = Field(
        ..., description="(ISO 8601 UTC 타임스탬프, 값 문자열) 튜플 목록"
    )


class TemperatureTimeseriesResponse(BaseModel):
    """GET /monitoring/temperature/timeseries 응답."""

    status: str
    series: list[TemperatureSeriesItem]
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# 전력(Power) — Phase 5 [P8]
# ---------------------------------------------------------------------------

class PowerSummaryData(BaseModel):
    """전력 요약 데이터 — 서버총전력(IPMI)/CPU(Kepler)/가속기 벤더별/기타."""

    server_total_watts: Optional[float] = Field(..., description="서버 총 전력, IPMI 기준 (와트 W)")
    cpu_total_watts: Optional[float] = Field(..., description="CPU 전력, Kepler 기준 (와트 W)")
    accelerator_total_watts: Optional[float] = Field(..., description="가속기 전력 합계 (와트 W)")
    accelerator_by_vendor: dict[str, Optional[float]] = Field(
        ..., description="벤더별 가속기 전력 합계 (와트 W)"
    )
    other_watts: Optional[float] = Field(..., description="기타 전력(서버 총전력 - CPU - 가속기) (와트 W)")


class PowerSummaryResponse(BaseModel):
    """GET /monitoring/power/summary 응답."""

    status: str
    data: PowerSummaryData
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class PowerBreakdownItem(BaseModel):
    """전력 분해의 단일 차원 항목."""

    key: str
    watts: Optional[float] = Field(..., description="전력 (와트 W)")
    layer: Optional[str] = None


class PowerBreakdownResponse(BaseModel):
    """GET /monitoring/power/breakdown 응답."""

    status: str
    dimension: str
    items: list[PowerBreakdownItem]
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class PowerTimeseriesLayer(BaseModel):
    """전력 시계열의 단일 계층(server/cpu/accelerator) 시리즈."""

    layer: str
    values: list[tuple[float, str]]


class PowerTimeseriesResponse(BaseModel):
    """GET /monitoring/power/timeseries 응답."""

    status: str
    layers: list[PowerTimeseriesLayer]
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class AcceleratorEfficiency(BaseModel):
    """가속기 벤더별 효율 데이터."""

    vendor: str
    power_watts: Optional[float] = Field(..., description="가속기 전력 (와트 W)")
    utilization_pct: Optional[float] = Field(
        ..., description="가속기 사용률 (%) — NVIDIA=0~100 확정, Furiosa/Rebellions 스케일 미확정"
    )
    tdp_watts: Optional[float] = Field(..., description="TDP (와트 W)")
    tdp_ratio_pct: Optional[float] = Field(..., description="TDP 대비 사용 전력 비중 (%)")


class PowerEfficiencyData(BaseModel):
    """전력 효율 데이터 — PUE 추정 + 가속기별 효율."""

    pue_estimate: Optional[float]
    accelerators: list[AcceleratorEfficiency]


class PowerEfficiencyResponse(BaseModel):
    """GET /monitoring/power/efficiency 응답."""

    status: str
    data: PowerEfficiencyData
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []
