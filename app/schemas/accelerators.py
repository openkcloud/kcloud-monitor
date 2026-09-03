"""
가속기(GPU/NPU) API v2 Pydantic 스키마.

공통 응답 정책(docs/API_GUIDE.md §공통-응답-정책):
  - status: "success" | "partial" | "error"
  - observed_at: ISO 8601 수집 시각
  - warnings[]: NO_DATA, ACCELERATOR_NOT_FOUND, PARTITION_DATA_NOT_AVAILABLE 등
메모리는 벤더별 원본 단위(L40S=MiB, Furiosa/Rebellions=bytes)를 bytes로 통일해 반환한다.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, Field


_KST = timezone(timedelta(hours=9))


def _now() -> str:
    return datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S")


class AcceleratorItem(BaseModel):
    """단일 가속기 요약 정보."""

    acc_id: str
    vendor: str
    cluster: str
    node: Optional[str] = None
    model: Optional[str] = None
    utilization_percent: Optional[float] = Field(
        None, description="사용률 (%). NVIDIA=0~100 확정, Furiosa/Rebellions 스케일 미확정"
    )
    temperature_celsius: Optional[float] = Field(None, description="온도 (섭씨 °C)")
    power_watts: Optional[float] = Field(None, description="전력 (와트 W)")
    memory_used_bytes: Optional[float] = Field(
        None, description="사용 메모리 (bytes). 벤더마다 다른 원본 단위를 bytes로 통일"
    )
    memory_total_bytes: Optional[float] = Field(
        None, description="총 메모리 (bytes). 벤더마다 다른 원본 단위를 bytes로 통일"
    )
    healthy: Optional[bool] = None
    labels: dict[str, str] = {}


class AcceleratorListResponse(BaseModel):
    """GET .../accelerators 응답."""

    status: str
    data: list[AcceleratorItem] = []
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class AcceleratorSummaryData(BaseModel):
    """가속기 집계 요약 데이터."""

    count: int
    vendor: Optional[str] = None
    avg_utilization_percent: Optional[float] = Field(
        None, description="평균 사용률 (%). NVIDIA=0~100 확정, Furiosa/Rebellions 스케일 미확정"
    )
    avg_temperature_celsius: Optional[float] = Field(None, description="평균 온도 (섭씨 °C)")
    avg_power_watts: Optional[float] = Field(None, description="평균 전력 (와트 W)")
    total_power_watts: Optional[float] = Field(None, description="전력 합계 (와트 W)")


class AcceleratorSummaryResponse(BaseModel):
    """GET .../accelerators/summary 응답."""

    status: str
    data: AcceleratorSummaryData
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class TopologyLinkItem(BaseModel):
    """토폴로지 연결 1건(NVLink/PCIe 대역폭)."""

    metric_labels: dict[str, str]
    value: Optional[float] = None


class AcceleratorTopologyData(BaseModel):
    """가속기 인터커넥트 토폴로지 데이터."""

    vendor: Optional[str] = None
    links: list[TopologyLinkItem] = []


class AcceleratorTopologyResponse(BaseModel):
    """GET .../accelerators/topology 응답."""

    status: str
    data: AcceleratorTopologyData
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class AcceleratorDetailResponse(BaseModel):
    """GET .../accelerators/{acc_id} 응답."""

    status: str
    data: Optional[AcceleratorItem] = None
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class AcceleratorMetricsData(BaseModel):
    """가속기 실시간 메트릭 상세 데이터."""

    utilization_percent: Optional[float] = Field(
        None, description="사용률 (%). NVIDIA=0~100 확정, Furiosa/Rebellions 스케일 미확정"
    )
    memory_used_bytes: Optional[float] = Field(
        None, description="사용 메모리 (bytes). 벤더마다 다른 원본 단위를 bytes로 통일"
    )
    memory_total_bytes: Optional[float] = Field(
        None, description="총 메모리 (bytes). 벤더마다 다른 원본 단위를 bytes로 통일"
    )
    power_watts: Optional[float] = Field(None, description="전력 (와트 W)")
    temperature_celsius: Optional[float] = Field(None, description="온도 (섭씨 °C)")
    healthy: Optional[bool] = None
    extra: dict[str, float] = Field(
        default_factory=dict,
        description="벤더별 부가 메트릭. sm_clock, mem_clock, freq = 동작 주파수(MHz) / "
        "mem_copy_util, dec_util, enc_util = 사용률(%) / pcie_replay, throttle = 누적 발생 횟수",
    )


class AcceleratorMetricsResponse(BaseModel):
    """GET .../accelerators/{acc_id}/metrics 응답."""

    status: str
    acc_id: str
    vendor: Optional[str] = None
    data: AcceleratorMetricsData
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class PowerData(BaseModel):
    """가속기 전력 현재값."""

    power_watts: Optional[float] = Field(None, description="전력 (와트 W)")


class AcceleratorPowerResponse(BaseModel):
    """GET .../accelerators/{acc_id}/power 응답."""

    status: str
    acc_id: str
    data: PowerData
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class PowerSeriesItem(BaseModel):
    """전력 시계열 단일 시리즈."""

    metric_labels: dict[str, str]
    values: list[tuple[str, str]] = Field(
        ..., description="(시각, 값) 쌍 목록. 시각은 ISO 8601 UTC 형식"
    )


class AcceleratorPowerTimeseriesResponse(BaseModel):
    """GET .../accelerators/{acc_id}/power/timeseries 응답."""

    status: str
    acc_id: str
    series: list[PowerSeriesItem] = []
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class TemperatureData(BaseModel):
    """가속기 온도 현재값."""

    temperature_celsius: Optional[float] = Field(None, description="온도 (섭씨 °C)")


class AcceleratorTemperatureResponse(BaseModel):
    """GET .../accelerators/{acc_id}/temperature 응답."""

    status: str
    acc_id: str
    data: TemperatureData
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class PartitionItem(BaseModel):
    """가속기를 나눈 파티션 한 조각의 정보. 현재 수집 데이터 없음."""

    partition_id: str
    profile: Optional[str] = None
    utilization_percent: Optional[float] = Field(
        None, description="사용률 (%). NVIDIA=0~100 확정, Furiosa/Rebellions 스케일 미확정"
    )


class PartitionListResponse(BaseModel):
    """GET .../accelerators/{acc_id}/partitions 응답."""

    status: str
    data: list[PartitionItem] = []
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class PartitionDetailResponse(BaseModel):
    """GET .../accelerators/{acc_id}/partitions/{partition_id} 응답."""

    status: str
    data: Optional[PartitionItem] = None
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class PartitionPowerData(BaseModel):
    """파티션 한 조각에 배분된 전력 추정값. 현재 수집 데이터 없음."""

    power_watts: Optional[float] = Field(None, description="전력 (와트 W)")


class PartitionPowerResponse(BaseModel):
    """GET .../partitions/{partition_id}/power 응답."""

    status: str
    acc_id: str
    partition_id: str
    data: PartitionPowerData
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class PartitionPowerTimeseriesResponse(BaseModel):
    """GET .../partitions/{partition_id}/power/timeseries 응답."""

    status: str
    acc_id: str
    partition_id: str
    series: list[PowerSeriesItem] = []
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []
