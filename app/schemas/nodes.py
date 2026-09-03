"""
Nodes API v2 Pydantic 스키마.

공통 응답 정책(docs/API_GUIDE.md §공통-응답-정책):
  - status: "success" | "partial" | "error"
  - observed_at: ISO 8601 수집 시각
  - warnings[]: NO_DATA, NO_POWER_DATA, IPMI_NOT_AVAILABLE 등 (정상 시 빈 목록)
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, Field


_KST = timezone(timedelta(hours=9))


def _now() -> str:
    return datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S")


class NodeSummaryItem(BaseModel):
    """노드 목록의 항목 하나.

    관리 클러스터는 kube_node_info 기반(물리 노드), 서비스 클러스터는 가속기 메트릭의
    hostname 기반(가상 노드)이라 internal_ip/os/kubelet_version은 서비스 클러스터에서 생략된다.
    """

    nodename: str = Field(
        ..., description="노드/호스트 이름(예: compute1, 또는 가속기 VM 호스트명). 노드 하위 엔드포인트의 식별자"
    )
    internal_ip: Optional[str] = Field(None, description="노드 내부 IP (mgmt만)")
    role: Optional[str] = Field(None, description="worker / control-plane (mgmt만)")
    cluster: str
    up: bool = Field(..., description="Ready 상태(mgmt) 또는 가속기 메트릭 보고 여부(서비스)")
    os: Optional[str] = Field(None, description="OS 이미지 (mgmt만)")
    kubelet_version: Optional[str] = Field(None, description="kubelet 버전 (mgmt만)")
    node_type: Optional[str] = Field(
        None, description='physical | virtual. machine_cpu_cores의 node 라벨 집합 기준'
    )
    accelerator_count: Optional[int] = Field(None, description="이 호스트의 가속기(카드) 수 (서비스 클러스터만 산출)")
    power_watts: Optional[float] = Field(
        None, description="전력(W). mgmt는 ipmi_dcmi_power_consumption_watts, 서비스는 가속기 전력 합"
    )


class NodeListResponse(BaseModel):
    """GET /clusters/{cluster}/nodes 응답."""

    status: str
    nodes: list[NodeSummaryItem]
    total: int
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class NodesSummaryData(BaseModel):
    """노드 집계 데이터."""

    ready_count: int
    total_count: int
    memory_total_bytes: Optional[float] = Field(None, description="총 메모리 (bytes)")
    memory_used_bytes: Optional[float] = Field(None, description="사용 메모리 (bytes)")
    memory_usage_percent: Optional[float] = Field(None, description="메모리 사용률 (%)")


class NodesSummaryResponse(BaseModel):
    """GET /clusters/{cluster}/nodes/summary 응답."""

    status: str
    data: NodesSummaryData
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class NodeOsInfo(BaseModel):
    """node_uname_info 기반 OS 정보."""

    sysname: Optional[str] = None
    release: Optional[str] = None
    version: Optional[str] = None
    machine: Optional[str] = None
    nodename: Optional[str] = None


class NodeDetailData(BaseModel):
    """노드 상세 데이터."""

    instance: str
    cluster: str
    up: bool
    os: Optional[NodeOsInfo] = None
    boot_time: Optional[str] = None
    cpu_cores: Optional[int] = None
    memory_total_bytes: Optional[float] = Field(None, description="총 메모리 (bytes)")
    node_type: Optional[str] = Field(
        None, description='physical | virtual. machine_cpu_cores의 node 라벨 집합 기준'
    )
    ready: Optional[bool] = Field(
        None, description="kube_node_status_condition(Ready) 기준. mgmt에만 존재(서비스 클러스터는 None)"
    )
    accelerator_count: int = Field(0, description="이 노드(호스트)의 가속기(카드) 수 — hostname 매칭")


class NodeDetailResponse(BaseModel):
    """GET /clusters/{cluster}/nodes/{node} 응답."""

    status: str
    data: Optional[NodeDetailData] = None
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class NodeMetricsData(BaseModel):
    """노드 종합 메트릭 데이터."""

    cpu_usage_percent: Optional[float] = Field(None, description="CPU 사용률 (%)")
    memory_usage_percent: Optional[float] = Field(None, description="메모리 사용률 (%)")
    memory_total_bytes: Optional[float] = Field(None, description="총 메모리 (bytes)")
    memory_used_bytes: Optional[float] = Field(None, description="사용 메모리 (bytes)")
    disk_usage_percent: Optional[float] = Field(None, description="디스크 사용률 (%)")
    network_receive_bytes_per_sec: Optional[float] = Field(None, description="네트워크 수신 처리량 (bytes/sec)")
    network_transmit_bytes_per_sec: Optional[float] = Field(None, description="네트워크 송신 처리량 (bytes/sec)")


class NodeMetricsResponse(BaseModel):
    """GET /clusters/{cluster}/nodes/{node}/metrics 응답."""

    status: str
    data: NodeMetricsData
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class CpuCoreUsage(BaseModel):
    """코어별 CPU 사용률."""

    cpu: str
    usage_percent: float = Field(..., description="코어 사용률 (%)")


class CpuModeBreakdown(BaseModel):
    """모드별 CPU 사용량(rate)."""

    mode: str
    rate: float = Field(..., description="모드별 CPU 시간 비율 (초/초, unitless rate)")


class NodeCpuData(BaseModel):
    """노드 CPU 상세 데이터."""

    usage_percent: Optional[float] = Field(None, description="전체 CPU 사용률 (%)")
    load1: Optional[float] = Field(None, description="1분 평균 부하 (load average, unitless)")
    load5: Optional[float] = Field(None, description="5분 평균 부하 (load average, unitless)")
    load15: Optional[float] = Field(None, description="15분 평균 부하 (load average, unitless)")
    per_core: list[CpuCoreUsage] = []
    per_mode: list[CpuModeBreakdown] = []


class NodeCpuResponse(BaseModel):
    """GET /clusters/{cluster}/nodes/{node}/cpu 응답."""

    status: str
    data: NodeCpuData
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class NodeMemoryData(BaseModel):
    """노드 메모리 상세 데이터."""

    total_bytes: Optional[float] = Field(None, description="총 메모리 (bytes)")
    available_bytes: Optional[float] = Field(None, description="가용 메모리 (bytes)")
    used_bytes: Optional[float] = Field(None, description="사용 메모리 (bytes)")
    used_percent: Optional[float] = Field(None, description="메모리 사용률 (%)")
    cached_bytes: Optional[float] = Field(None, description="캐시 메모리 (bytes)")
    buffers_bytes: Optional[float] = Field(None, description="버퍼 메모리 (bytes)")
    swap_total_bytes: Optional[float] = Field(None, description="총 스왑 (bytes)")
    swap_free_bytes: Optional[float] = Field(None, description="가용 스왑 (bytes)")
    swap_used_bytes: Optional[float] = Field(None, description="사용 스왑 (bytes)")


class NodeMemoryResponse(BaseModel):
    """GET /clusters/{cluster}/nodes/{node}/memory 응답."""

    status: str
    data: NodeMemoryData
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class FilesystemUsage(BaseModel):
    """마운트포인트별 파일시스템 사용량."""

    mountpoint: str
    fstype: Optional[str] = None
    size_bytes: Optional[float] = Field(None, description="파일시스템 총 용량 (bytes)")
    avail_bytes: Optional[float] = Field(None, description="가용 용량 (bytes)")
    used_percent: Optional[float] = Field(None, description="사용률 (%)")


class DiskIoRate(BaseModel):
    """디스크 장치별 I/O 처리율."""

    device: str
    read_bytes_per_sec: Optional[float] = Field(None, description="디스크 읽기 처리량 (bytes/sec)")
    write_bytes_per_sec: Optional[float] = Field(None, description="디스크 쓰기 처리량 (bytes/sec)")


class NodeStorageData(BaseModel):
    """노드 로컬 디스크 상세 데이터."""

    filesystems: list[FilesystemUsage] = []
    disks: list[DiskIoRate] = []


class NodeStorageResponse(BaseModel):
    """GET /clusters/{cluster}/nodes/{node}/storage 응답."""

    status: str
    data: NodeStorageData
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class NetworkInterfaceStats(BaseModel):
    """인터페이스별 네트워크 처리율."""

    device: str
    receive_bytes_per_sec: Optional[float] = Field(None, description="수신 처리량 (bytes/sec)")
    transmit_bytes_per_sec: Optional[float] = Field(None, description="송신 처리량 (bytes/sec)")
    receive_errors_per_sec: Optional[float] = Field(None, description="수신 에러율 (errors/sec)")
    transmit_errors_per_sec: Optional[float] = Field(None, description="송신 에러율 (errors/sec)")


class NodeNetworkData(BaseModel):
    """노드 네트워크 상세 데이터."""

    interfaces: list[NetworkInterfaceStats] = []


class NodeNetworkResponse(BaseModel):
    """GET /clusters/{cluster}/nodes/{node}/network 응답."""

    status: str
    data: NodeNetworkData
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class NodePowerData(BaseModel):
    """노드 전력 현재값 데이터."""

    watts: Optional[float] = Field(None, description="전력 (와트 W)")
    source: Optional[str] = None


class NodePowerResponse(BaseModel):
    """GET /clusters/{cluster}/nodes/{node}/power 응답."""

    status: str
    data: Optional[NodePowerData] = None
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class NodePowerTimeseriesPoint(BaseModel):
    """전력 시계열의 단일 포인트."""

    timestamp: str = Field(..., description="ISO 8601 UTC 시각")
    watts: float = Field(..., description="전력 (와트 W)")


class NodePowerTimeseriesResponse(BaseModel):
    """GET /clusters/{cluster}/nodes/{node}/power/timeseries 응답."""

    status: str
    series: list[NodePowerTimeseriesPoint] = []
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class HardwareSensor(BaseModel):
    """IPMI 센서 값 하나(전력/전압/전류/팬 등)."""

    name: str
    value: float = Field(..., description="센서 값 — 단위는 unit 필드 참조")
    unit: Optional[str] = None


class HardwareSensorsResponse(BaseModel):
    """GET /clusters/{cluster}/nodes/{node}/hardware/sensors 응답."""

    status: str
    sensors: list[HardwareSensor] = []
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class HardwarePowerResponse(BaseModel):
    """GET /clusters/{cluster}/nodes/{node}/hardware/power 응답."""

    status: str
    watts: Optional[float] = Field(None, description="전력 (와트 W)")
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class HardwareTemperatureSensor(BaseModel):
    """IPMI 온도 센서 값 하나."""

    name: str
    celsius: float = Field(..., description="온도 (섭씨 °C)")


class HardwareTemperatureResponse(BaseModel):
    """GET /clusters/{cluster}/nodes/{node}/hardware/temperature 응답."""

    status: str
    sensors: list[HardwareTemperatureSensor] = []
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []
