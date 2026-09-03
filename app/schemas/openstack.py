"""
OpenStack API v2 Pydantic 스키마.

공통 응답 정책: status(success|partial|error) / observed_at / warnings.
경고 코드: NOT_CONFIGURED(크리덴셜 미설정), UPSTREAM_ERROR(OpenStack 호출 실패), NO_DATA.
"""
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HypervisorItem(BaseModel):
    """물리 하이퍼바이저(= 물리 서버) 항목."""

    hostname: str = Field(..., description="하이퍼바이저 호스트명 (예: compute5)")
    state: Optional[str] = Field(None, description="up/down")
    status: Optional[str] = Field(None, description="enabled/disabled")
    vm_count: int = Field(0, description="이 서버에 배치된 VM 수")


class VMAccelerator(BaseModel):
    """VM에 passthrough된 가속기(flavor alias 기반)."""

    alias: str = Field(..., description="PCI alias (예: L40S, furiosa-rngd, rebellions)")
    count: int = Field(..., description="passthrough된 카드 수")


class VMItem(BaseModel):
    """OpenStack VM 항목."""

    vm_id: str = Field(..., description="instance uuid")
    name: str = Field(..., description="VM 이름 (= Prometheus hostname 라벨, 조인 키)")
    host: Optional[str] = Field(None, description="배치된 물리 하이퍼바이저 (OS-EXT-SRV-ATTR:host)")
    status: Optional[str] = Field(None, description="ACTIVE/SHUTOFF 등")
    flavor: Optional[str] = Field(None, description="flavor 이름")
    project_id: Optional[str] = Field(None, description="소속 프로젝트 (과금 단위)")
    accelerator: Optional[VMAccelerator] = Field(None, description="passthrough 가속기 (없으면 null)")


class OpenStackSummaryData(BaseModel):
    """OpenStack 전체 현황 집계."""

    hypervisor_count: int = Field(0, description="물리 하이퍼바이저 수")
    vm_count: int = Field(0, description="전체 VM 수")
    accelerator_vm_count: int = Field(0, description="가속기 passthrough VM 수")


class OpenStackSummaryResponse(BaseModel):
    status: str
    data: Optional[OpenStackSummaryData] = None
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class HypervisorListResponse(BaseModel):
    status: str
    data: list[HypervisorItem] = []
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class VMListResponse(BaseModel):
    status: str
    data: list[VMItem] = []
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class VMDetailResponse(BaseModel):
    status: str
    data: Optional[VMItem] = None
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class ProjectItem(BaseModel):
    """OpenStack 프로젝트(Keystone) 항목."""

    project_id: str = Field(..., description="Keystone 프로젝트 uuid")
    name: str = Field(..., description="프로젝트 이름")
    vm_count: int = Field(0, description="소속 VM 수")
    accelerator_vm_count: int = Field(0, description="가속기 passthrough VM 수")


class ProjectListResponse(BaseModel):
    status: str
    data: list[ProjectItem] = []
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class ProjectDetailResponse(BaseModel):
    status: str
    data: Optional[ProjectItem] = None
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class ProjectSummaryData(BaseModel):
    """프로젝트가 쓰는 자원 요약. flavor 상세를 가져올 수 없어 VM 수 위주로 집계."""

    project_id: str = Field(..., description="Keystone 프로젝트 uuid")
    name: str = Field(..., description="프로젝트 이름")
    vm_count: int = Field(0, description="소속 VM 수")
    accelerator_vm_count: int = Field(0, description="가속기 passthrough VM 수")
    total_vcpus: Optional[int] = Field(None, description="flavor 상세 미노출로 현재 미집계")
    total_ram_mb: Optional[int] = Field(None, description="flavor 상세 미노출로 현재 미집계")


class ProjectSummaryResponse(BaseModel):
    status: str
    data: Optional[ProjectSummaryData] = None
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class HypervisorDetailData(BaseModel):
    """물리 서버 상세. 서버 자원 정보와 그 위에 올라간 VM 목록."""

    hostname: str = Field(..., description="하이퍼바이저 호스트명")
    state: Optional[str] = Field(None, description="up/down")
    status: Optional[str] = Field(None, description="enabled/disabled")
    vcpus: Optional[int] = Field(None, description="총 vcpu 수")
    vcpus_used: Optional[int] = Field(None, description="사용 중 vcpu 수")
    memory_mb: Optional[int] = Field(None, description="총 메모리(MB)")
    memory_mb_used: Optional[int] = Field(None, description="사용 중 메모리(MB)")
    local_gb: Optional[int] = Field(None, description="총 로컬 디스크(GB)")
    local_gb_used: Optional[int] = Field(None, description="사용 중 로컬 디스크(GB)")
    running_vms: Optional[int] = Field(None, description="실행 중인 VM 수")
    hypervisor_type: Optional[str] = Field(None, description="예: QEMU")
    hypervisor_version: Optional[int] = Field(None, description="하이퍼바이저 버전")
    host_ip: Optional[str] = Field(None, description="하이퍼바이저 관리 IP")
    vms: list[VMItem] = Field([], description="이 호스트에 배치된 VM 목록")


class HypervisorDetailResponse(BaseModel):
    status: str
    data: Optional[HypervisorDetailData] = None
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class VMSummaryData(BaseModel):
    """VM 집계 요약."""

    total: int = Field(0, description="전체 VM 수")
    by_status: dict[str, int] = Field({}, description="상태별 VM 수 (ACTIVE/SHUTOFF 등)")
    accelerator_vm_count: int = Field(0, description="가속기 passthrough VM 수")
    by_project: dict[str, int] = Field({}, description="프로젝트(uuid)별 VM 수")


class VMSummaryResponse(BaseModel):
    status: str
    data: Optional[VMSummaryData] = None
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


# ── VM 사용량 메트릭 (libvirt exporter) ─────────────────────────────────────

class VMCpuMetrics(BaseModel):
    """VM CPU 사용량."""

    cores_used: Optional[float] = Field(None, description="사용 중 vCPU 코어 수 (rate(cpu_time)/1e9)")
    cpu_time_ns_total: Optional[float] = Field(None, description="누적 CPU 시간(ns)")


class VMMemoryMetrics(BaseModel):
    """VM 메모리 사용량 (libvirt balloon, bytes로 환산)."""

    rss_bytes: Optional[float] = Field(None, description="호스트가 이 VM에 할당한 실제 메모리(RSS)")
    current_bytes: Optional[float] = Field(None, description="게스트에 할당된 balloon 현재값")
    available_bytes: Optional[float] = Field(None, description="게스트가 인식하는 총 메모리")
    unused_bytes: Optional[float] = Field(None, description="게스트 내 미사용 메모리")
    usable_bytes: Optional[float] = Field(None, description="게스트가 즉시 회수 가능한 메모리")
    used_bytes: Optional[float] = Field(None, description="파생: current − unused")


class VMDiskMetrics(BaseModel):
    """VM 디스크 누적 I/O (전 블록 디바이스 합)."""

    read_bytes_total: Optional[float] = Field(None, description="누적 읽기 바이트")
    write_bytes_total: Optional[float] = Field(None, description="누적 쓰기 바이트")


class VMNetworkMetrics(BaseModel):
    """VM 네트워크 누적 트래픽 (전 인터페이스 합)."""

    rx_bytes_total: Optional[float] = Field(None, description="누적 수신 바이트")
    tx_bytes_total: Optional[float] = Field(None, description="누적 송신 바이트")


class VMMetricsData(BaseModel):
    """VM 사용량 메트릭 종합."""

    vm_id: str = Field(..., description="instance uuid")
    name: Optional[str] = Field(None, description="VM 이름")
    host: Optional[str] = Field(None, description="배치된 물리 하이퍼바이저")
    state: Optional[str] = Field(None, description="libvirt 도메인 상태 (running/shutoff 등)")
    cpu: VMCpuMetrics
    memory: VMMemoryMetrics
    disk: VMDiskMetrics
    network: VMNetworkMetrics


class VMMetricsResponse(BaseModel):
    status: str
    data: Optional[VMMetricsData] = None
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


# ── VM 전력 귀속 (P6) ───────────────────────────────────────────────────────

class VMPowerData(BaseModel):
    """VM에 배분된 전력. 물리 서버 총전력을 CPU 점유 비율로 나눈 추정치."""

    vm_id: str = Field(..., description="instance uuid")
    name: Optional[str] = Field(None, description="VM 이름")
    host: Optional[str] = Field(None, description="배치된 물리 하이퍼바이저")
    attributed_watts: Optional[float] = Field(None, description="귀속 전력(W) = 서버총전력 × CPU 점유비율")
    server_total_watts: Optional[float] = Field(None, description="호스트 IPMI 총전력(W)")
    cpu_share_pct: Optional[float] = Field(None, description="노드 내 이 VM의 CPU 점유 비율(%)")
    method: str = Field("cpu_proportional", description="귀속 방식")


class VMPowerResponse(BaseModel):
    status: str
    data: Optional[VMPowerData] = None
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []
