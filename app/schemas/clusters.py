"""
클러스터 API v2 Pydantic 스키마.
"""
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClusterListItem(BaseModel):
    """클러스터 목록의 항목 하나."""

    name: str = Field(..., description="클러스터 이름 (= Prometheus cluster 라벨, mgmt는 관리 클러스터)")
    type: str = Field(..., description="클러스터 구분: \"management\"(물리+OpenStack) | \"service\"(VM 위 K8s)")
    # description은 목록에서 제외. 상세 조회에서만 제공
    parent_cluster: Optional[str] = Field(
        None, description="서비스 클러스터의 부모 관리 클러스터명(mgmt). 관리 클러스터는 None"
    )
    openstack_project: Optional[str] = Field(
        None, description="서비스 클러스터가 속한 OpenStack 프로젝트. 과금 단위"
    )
    has_openstack: Optional[bool] = Field(
        None, description="True이면 /openstack/ 하위 경로 접근 가능. 관리 클러스터만 True(서비스는 생략)"
    )
    service_clusters: Optional[list[str]] = Field(
        None, description="관리 클러스터가 거느린 서비스 클러스터명 목록. 서비스 클러스터는 생략"
    )
    node_count: int = Field(0, description="노드 수 (K8s는 kube_node_info, 가속기 VM은 메트릭 호스트 수)")
    accelerator_count: int = Field(0, description="가속기(카드) 수. 관리 클러스터는 하위 전체 합계")
    total_power_watts: Optional[float] = Field(
        None, description="가속기 전력 합계 (W). 관리 클러스터는 하위 전체 합계"
    )
    status: str = Field("healthy", description="healthy | warning | critical")


class ClusterPagination(BaseModel):
    """목록 페이지네이션 메타."""

    total: int = Field(..., description="필터 적용 후 전체 개수")
    limit: int = Field(..., description="페이지 크기")
    offset: int = Field(..., description="시작 오프셋")
    has_next: bool = Field(..., description="다음 페이지 존재 여부")


class ClusterListResponse(BaseModel):
    """GET /clusters 응답."""

    status: str
    data: list[ClusterListItem]
    pagination: Optional[ClusterPagination] = None
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class ClusterDetailData(BaseModel):
    """클러스터 상세 데이터 (목록 아이템과 동일 모양 + 상세 전용 필드)."""

    name: str = Field(..., description="클러스터 이름")
    type: str = Field(..., description="클러스터 구분: \"management\"(물리+OpenStack) | \"service\"(VM 위 K8s)")
    description: Optional[str] = Field(None, description="사람이 읽는 설명 (자동 생성)")
    parent_cluster: Optional[str] = Field(
        None, description="서비스 클러스터의 부모 관리 클러스터명(mgmt). 관리 클러스터는 생략"
    )
    openstack_project: Optional[str] = Field(
        None, description="서비스 클러스터가 속한 OpenStack 프로젝트(과금 단위)"
    )
    has_openstack: Optional[bool] = Field(
        None, description="True이면 /openstack/ 하위 경로 접근 가능. 관리 클러스터만(서비스는 생략)"
    )
    service_clusters: Optional[list[str]] = Field(
        None, description="관리 클러스터가 거느린 서비스 클러스터명 목록. 서비스 클러스터는 생략"
    )
    node_count: int = Field(0, description="노드 수 (K8s는 kube_node_info, 가속기 VM은 메트릭 호스트 수)")
    accelerator_count: int = Field(0, description="가속기(카드) 수. 관리 클러스터는 하위 전체 합계")
    total_power_watts: Optional[float] = Field(
        None, description="가속기 전력 합계 (W). 관리 클러스터는 하위 전체 합계"
    )
    status: str = Field("healthy", description="healthy | warning | critical")
    # ── 상세 전용 ──────────────────────────────────────────────
    runtime: Optional[str] = Field(
        None,
        description='서비스 클러스터 실행 형태: "kubernetes"(진짜 K8s 클러스터) | "vm"(단독 가속기 VM). '
        "관리 클러스터는 생략",
    )
    vendor: Optional[str] = Field(None, description="가속기 벤더 (nvidia/furiosa/rebellions)")
    accelerator_type: Optional[str] = Field(None, description="GPU | NPU")
    nodes: list[str] = Field(default_factory=list, description="노드 이름 목록")
    avg_utilization: Optional[float] = Field(
        None,
        description="평균 가속기 사용률 (%). NVIDIA=0~100 확정, Furiosa/Rebellions 스케일 미확정",
    )
    avg_temperature: Optional[float] = Field(None, description="평균 온도 (섭씨 °C)")


class ClusterDetailResponse(BaseModel):
    """GET /clusters/{cluster} 응답."""

    status: str
    data: ClusterDetailData
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class NodeResources(BaseModel):
    """노드 자원 요약."""

    total: int = Field(0, description="전체 노드 수")
    ready: int = Field(0, description="Ready 노드 수")
    not_ready: int = Field(0, description="NotReady 노드 수")


class CpuResources(BaseModel):
    """CPU 자원 요약."""

    total_cores: Optional[float] = Field(None, description="전체 코어 수")
    used_cores: Optional[float] = Field(None, description="사용 중 코어 (rate 기반)")
    utilization_percent: Optional[float] = Field(None, description="사용률 (%)")


class MemoryResources(BaseModel):
    """메모리 자원 요약."""

    total_gb: Optional[float] = Field(None, description="전체 메모리 (GB)")
    used_gb: Optional[float] = Field(None, description="사용 메모리 (GB)")
    utilization_percent: Optional[float] = Field(None, description="사용률 (%)")


class AcceleratorResources(BaseModel):
    """가속기 자원 요약."""

    total: int = Field(0, description="가속기(카드) 수")
    active: Optional[int] = Field(None, description="사용 중 카드 수 (미산출 시 생략)")
    idle: Optional[int] = Field(None, description="유휴 카드 수 (미산출 시 생략)")
    avg_utilization_percent: Optional[float] = Field(None, description="평균 사용률 (%)")
    total_power_watts: Optional[float] = Field(None, description="전력 합계 (W)")


class StorageResources(BaseModel):
    """스토리지 자원 요약 (데이터소스 확정 전까지 생략될 수 있음)."""

    total_tb: Optional[float] = Field(None, description="전체 용량 (TB)")
    used_tb: Optional[float] = Field(None, description="사용 용량 (TB)")
    utilization_percent: Optional[float] = Field(None, description="사용률 (%)")


class ClusterResources(BaseModel):
    """클러스터 자원 요약 묶음. 데이터 없는 항목은 생략(null)."""

    nodes: Optional[NodeResources] = None
    cpu: Optional[CpuResources] = None
    memory: Optional[MemoryResources] = None
    accelerators: Optional[AcceleratorResources] = None
    storage: Optional[StorageResources] = None


class PowerBreakdown(BaseModel):
    """전력 구성 내역 (dram은 메트릭 미제공으로 생략, other에 흡수)."""

    cpu_watts: Optional[float] = Field(None, description="CPU 전력 (kepler)")
    gpu_watts: Optional[float] = Field(None, description="가속기 전력")
    other_watts: Optional[float] = Field(None, description="기타 (total − cpu − gpu)")


class ClusterPower(BaseModel):
    """클러스터 전력 요약."""

    total_watts: Optional[float] = Field(None, description="총 전력 (ipmi 물리 소비)")
    breakdown: Optional[PowerBreakdown] = None


class ClusterSummaryData(BaseModel):
    """클러스터가 가진 자원을 종류별로 합친 요약."""

    cluster: str
    type: str
    resources: ClusterResources
    power: ClusterPower


class ClusterSummaryResponse(BaseModel):
    """GET /clusters/{cluster}/summary 응답."""

    status: str
    data: ClusterSummaryData
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class TopologyAccelerator(BaseModel):
    """노드에 장착된 가속기 한 장."""

    id: str = Field(..., description="가속기 UUID/식별자")
    model: Optional[str] = Field(None, description="모델명 (예: NVIDIA L40S, Furiosa rngd)")
    utilization_percent: Optional[float] = Field(None, description="사용률 (%)")
    power_watts: Optional[float] = Field(None, description="전력 (W)")


class TopologyNode(BaseModel):
    """클러스터 구성도의 노드 하나."""

    name: str = Field(..., description="노드/호스트 이름")
    node_type: str = Field(..., description="physical | virtual")
    cpu_cores: Optional[int] = Field(None, description="CPU 코어 수 (물리 노드만 확인 가능)")
    memory_gb: Optional[float] = Field(None, description="메모리 (GB)")
    accelerators: list[TopologyAccelerator] = Field(default_factory=list)


class ClusterTopologyData(BaseModel):
    """클러스터 토폴로지 데이터."""

    cluster: str
    nodes: list[TopologyNode]


class ClusterTopologyResponse(BaseModel):
    """GET /clusters/{cluster}/topology 응답."""

    status: str
    data: ClusterTopologyData
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []


class ClusterPowerCard(BaseModel):
    """가속기 카드 한 장의 전력 내역."""

    id: str = Field(..., description="가속기 카드 식별자 (UUID/uuid/gpu/device 라벨 기준)")
    hostname: str = Field(..., description="카드가 보고된 호스트 (Hostname/hostname/instance 라벨 기준)")
    watts: Optional[float] = Field(None, description="카드 전력 (와트 W)")


class ClusterPowerData(BaseModel):
    """클러스터 전력 합계 데이터."""

    cluster: str
    total_power_watts: Optional[float] = Field(None, description="가속기 전력 합계 (와트 W)")
    accelerator_count: int = Field(..., description="전력을 보고한 가속기(카드) 개수")
    cards: list[ClusterPowerCard] = Field(
        default_factory=list, description="카드별 전력 내역(id·hostname·watts). 관리 클러스터는 하위 서비스 전체 합산"
    )


class ClusterPowerResponse(BaseModel):
    """GET /clusters/{cluster}/power 응답."""

    status: str
    data: ClusterPowerData
    observed_at: str = Field(default_factory=_now)
    warnings: list[str] = []
