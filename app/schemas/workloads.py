"""
워크로드 API v2 Pydantic 스키마.

Pod / Container / Namespace / Service 응답 모델.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, Field


_KST = timezone(timedelta(hours=9))


def _now() -> str:
    return datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Pod
# ---------------------------------------------------------------------------


class PodItem(BaseModel):
    namespace: str
    pod: str
    cluster: str
    node: Optional[str] = None
    phase: Optional[str] = None
    pod_ip: Optional[str] = None
    host_ip: Optional[str] = None
    created_at: Optional[str] = None
    workload_type: Optional[str] = None
    workload_name: Optional[str] = None
    service_name: Optional[str] = None
    container_count: Optional[int] = None
    restart_count: Optional[int] = None
    cpu_usage: Optional[float] = None
    memory_usage_bytes: Optional[float] = Field(None, description="메모리 사용량 (bytes)")


class PodListResponse(BaseModel):
    status: str
    pods: list[PodItem] = []
    total: int = 0
    observed_at: str = Field(default_factory=_now)
    is_stale: bool = False
    warnings: list[str] = []


class PodSummaryData(BaseModel):
    total_count: int = 0
    running_count: int = 0
    pending_count: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    unknown_count: int = 0
    namespace_distribution: dict[str, int] = {}


class PodSummaryResponse(BaseModel):
    status: str
    data: PodSummaryData
    observed_at: str = Field(default_factory=_now)
    is_stale: bool = False
    warnings: list[str] = []


class PodDetailData(BaseModel):
    namespace: str
    pod: str
    cluster: str
    uid: Optional[str] = None
    node: Optional[str] = None
    phase: Optional[str] = None
    pod_ip: Optional[str] = None
    host_ip: Optional[str] = None
    created_at: Optional[str] = None
    workload_type: Optional[str] = None
    workload_name: Optional[str] = None
    service_name: Optional[str] = None
    container_count: Optional[int] = None
    restart_count: Optional[int] = None
    cpu_usage: Optional[float] = None
    memory_usage_bytes: Optional[float] = Field(None, description="메모리 사용량 (bytes)")
    cpu_requests: Optional[float] = None
    cpu_limits: Optional[float] = None
    memory_requests_bytes: Optional[float] = Field(None, description="메모리 요청량 (bytes)")
    memory_limits_bytes: Optional[float] = Field(None, description="메모리 제한량 (bytes)")


class PodDetailResponse(BaseModel):
    status: str
    data: Optional[PodDetailData] = None
    observed_at: str = Field(default_factory=_now)
    is_stale: bool = False
    warnings: list[str] = []


class PodPowerData(BaseModel):
    watts: Optional[float] = Field(None, description="Pod 전력 귀속 (와트 W)")
    source: Optional[str] = None


class PodPowerResponse(BaseModel):
    status: str
    data: Optional[PodPowerData] = None
    observed_at: str = Field(default_factory=_now)
    is_stale: bool = False
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------


class ContainerItem(BaseModel):
    namespace: str
    pod: str
    container: str
    cluster: str
    container_id: Optional[str] = None
    image: Optional[str] = None
    status: Optional[str] = None
    restart_count: Optional[int] = None
    cpu_usage: Optional[float] = None
    memory_usage_bytes: Optional[float] = Field(None, description="메모리 사용량 (bytes)")
    cpu_requests: Optional[float] = None
    cpu_limits: Optional[float] = None
    memory_requests_bytes: Optional[float] = Field(None, description="메모리 요청량 (bytes)")
    memory_limits_bytes: Optional[float] = Field(None, description="메모리 제한량 (bytes)")


class ContainerListResponse(BaseModel):
    status: str
    containers: list[ContainerItem] = []
    total: int = 0
    observed_at: str = Field(default_factory=_now)
    is_stale: bool = False
    warnings: list[str] = []


class ContainerMetricsData(BaseModel):
    cpu_usage: Optional[float] = None
    memory_usage_bytes: Optional[float] = Field(None, description="메모리 사용량 (bytes)")


class ContainerMetricsResponse(BaseModel):
    status: str
    data: ContainerMetricsData
    observed_at: str = Field(default_factory=_now)
    is_stale: bool = False
    warnings: list[str] = []


class ContainerDetailResponse(BaseModel):
    status: str
    data: Optional[ContainerItem] = None
    observed_at: str = Field(default_factory=_now)
    is_stale: bool = False
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# Pod Accelerator
# ---------------------------------------------------------------------------


class PodAcceleratorItem(BaseModel):
    acc_id: Optional[str] = None
    vendor: Optional[str] = None
    model_name: Optional[str] = None


class PodAcceleratorResponse(BaseModel):
    status: str
    data: list[PodAcceleratorItem] = []
    observed_at: str = Field(default_factory=_now)
    is_stale: bool = False
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# Namespace
# ---------------------------------------------------------------------------


class NamespaceItem(BaseModel):
    namespace: str
    cluster: str
    pod_count: Optional[int] = None
    created_at: Optional[str] = None


class NamespaceListResponse(BaseModel):
    status: str
    namespaces: list[NamespaceItem] = []
    total: int = 0
    observed_at: str = Field(default_factory=_now)
    is_stale: bool = False
    warnings: list[str] = []


class NamespaceSummaryData(BaseModel):
    namespace: str
    cluster: str
    pod_count: int = 0
    container_count: int = 0
    cpu_usage: Optional[float] = None
    memory_usage_bytes: Optional[float] = Field(None, description="메모리 사용량 (bytes)")
    cpu_requests: Optional[float] = None
    memory_requests_bytes: Optional[float] = Field(None, description="메모리 요청량 (bytes)")


class NamespaceSummaryResponse(BaseModel):
    status: str
    data: NamespaceSummaryData
    observed_at: str = Field(default_factory=_now)
    is_stale: bool = False
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# Service (전역 엔드포인트용)
# ---------------------------------------------------------------------------


class ServiceItem(BaseModel):
    service_name: str
    namespace: str
    cluster: str
    pod_count: int = 0
    running_pod_count: int = 0
    cpu_usage: Optional[float] = None
    memory_usage_bytes: Optional[float] = Field(None, description="메모리 사용량 (bytes)")


class ServiceListResponse(BaseModel):
    status: str
    services: list[ServiceItem] = []
    total: int = 0
    observed_at: str = Field(default_factory=_now)
    is_stale: bool = False
    warnings: list[str] = []


class ServiceSummaryData(BaseModel):
    total_services: int = 0
    cluster_distribution: dict[str, int] = {}


class ServiceSummaryResponse(BaseModel):
    status: str
    data: ServiceSummaryData
    observed_at: str = Field(default_factory=_now)
    is_stale: bool = False
    warnings: list[str] = []


class ServiceDetailData(BaseModel):
    service_name: str
    namespace: str
    cluster: str
    pod_count: int = 0
    running_pod_count: int = 0
    cpu_usage: Optional[float] = None
    memory_usage_bytes: Optional[float] = Field(None, description="메모리 사용량 (bytes)")
    cpu_requests: Optional[float] = None
    memory_requests_bytes: Optional[float] = Field(None, description="메모리 요청량 (bytes)")


class ServiceDetailResponse(BaseModel):
    status: str
    data: Optional[ServiceDetailData] = None
    observed_at: str = Field(default_factory=_now)
    is_stale: bool = False
    warnings: list[str] = []


class ServicePowerData(BaseModel):
    watts: Optional[float] = Field(None, description="서비스 전력 귀속 (와트 W)")
    source: Optional[str] = None


class ServicePowerResponse(BaseModel):
    status: str
    data: Optional[ServicePowerData] = None
    observed_at: str = Field(default_factory=_now)
    is_stale: bool = False
    warnings: list[str] = []
