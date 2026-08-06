"""
KCloud Monitor v2 — OpenStack (13개, 관리 클러스터 전용).

Nova/Placement/Magnum API + libvirt로 VM·하이퍼바이저·프로젝트를 조회한다(메트릭 아님).
VM 전력은 물리 실측 → VM 귀속(A안, power_attribution_plan) 파생 메트릭을 사용한다.
주의: Placement PCI 추적 OFF 환경(O-1 확정) → GPU passthrough는 libvirt hostdev가 확정 경로.
데이터소스(구현 예정): OpenStack API(Keystone/Nova/Placement/Magnum), libvirt, Mimir(파생 메트릭).
설계: sample_api.md §6.1~§6.4 / 전력 계층 P6.
"""
from fastapi import APIRouter, Depends, Request

from app.api.v2._stub import stub
from app.api.v2.deps import list_params

router = APIRouter()


@router.get("/clusters/{cluster}/openstack/summary", summary="OpenStack 전체 현황")
async def get_openstack_summary(request: Request, cluster: str):
    """OpenStack 요약 — 프로젝트/하이퍼바이저/VM 수, 자원 할당 총계. [§6.1]"""
    return stub(
        request,
        "OpenStack 전체 현황(프로젝트·하이퍼바이저·VM 집계)",
        sources=("Nova API", "Keystone API"),
        ref="sample_api.md §6.1",
    )


@router.get("/clusters/{cluster}/openstack/projects", summary="프로젝트 목록")
async def list_openstack_projects(
    request: Request, cluster: str, params: dict = Depends(list_params)
):
    """OpenStack 프로젝트 목록 — 과금 단위(project). VM/자원 할당 요약 포함."""
    return stub(
        request,
        "OpenStack 프로젝트 목록",
        sources=("Keystone API", "Nova API(quota)"),
        params=params,
    )


@router.get("/clusters/{cluster}/openstack/projects/{project_id}", summary="프로젝트 상세")
async def get_openstack_project(request: Request, cluster: str, project_id: str):
    """단일 프로젝트 상세 — 소속 VM, 쿼터, 사용량."""
    return stub(request, "OpenStack 프로젝트 상세", sources=("Keystone API", "Nova API"))


@router.get("/clusters/{cluster}/openstack/projects/{project_id}/summary", summary="프로젝트 자원 요약")
async def get_openstack_project_summary(request: Request, cluster: str, project_id: str):
    """프로젝트 자원 요약 — vCPU/RAM/GPU 할당·사용, 전력 귀속 합계."""
    return stub(
        request,
        "프로젝트 자원·전력 요약",
        sources=("Nova API", "Mimir(openstack_vm_power_watts_estimated)"),
    )


@router.get("/clusters/{cluster}/openstack/hypervisors", summary="하이퍼바이저 목록")
async def list_openstack_hypervisors(
    request: Request, cluster: str, params: dict = Depends(list_params)
):
    """하이퍼바이저 목록 — 물리 노드 대응, vCPU/RAM 할당률, 수용 VM 수."""
    return stub(
        request,
        "하이퍼바이저 목록(물리 노드 대응)",
        sources=("Nova API(os-hypervisors, admin 필요)", "resource-map 원장(물리 서버 연계)"),
        params=params,
    )


@router.get("/clusters/{cluster}/openstack/hypervisors/{host}", summary="하이퍼바이저 상세")
async def get_openstack_hypervisor(request: Request, cluster: str, host: str):
    """단일 하이퍼바이저 상세 — 스펙, 배치 VM 수, 물리 노드 전력 연계."""
    return stub(
        request,
        "하이퍼바이저 상세",
        sources=("Nova API", "Mimir(physical_host_power_watts)"),
    )


@router.get("/clusters/{cluster}/openstack/hypervisors/{host}/vms", summary="하이퍼바이저별 VM 배치")
async def list_hypervisor_vms(request: Request, cluster: str, host: str):
    """하이퍼바이저에 배치된 VM 목록 — libvirt 도메인(instance-xxxx) 매핑 포함. [§6.3]"""
    return stub(
        request,
        "하이퍼바이저별 VM 배치",
        sources=("Nova API", "libvirt(도메인·QEMU PID)"),
        ref="sample_api.md §6.3",
    )


@router.get("/clusters/{cluster}/openstack/vms", summary="VM 목록")
async def list_openstack_vms(request: Request, cluster: str, params: dict = Depends(list_params)):
    """VM 목록 — instance_uuid, flavor, 프로젝트, 상태, 서비스 K8s 노드 여부."""
    return stub(
        request,
        "VM 목록(instance_uuid·flavor·프로젝트)",
        sources=("Nova API", "Magnum API(서비스 K8s 노드 판별)"),
        params=params,
    )


@router.get("/clusters/{cluster}/openstack/vms/summary", summary="VM 집계 요약")
async def get_openstack_vms_summary(request: Request, cluster: str):
    """VM 집계 — 상태별 수, 프로젝트별 분포, GPU passthrough VM 수."""
    return stub(request, "VM 집계 요약", sources=("Nova API",))


@router.get("/clusters/{cluster}/openstack/vms/{vm_id}", summary="VM 상세")
async def get_openstack_vm(request: Request, cluster: str, vm_id: str):
    """단일 VM 상세 — flavor/이미지/네트워크, 서비스 클러스터 연결 정보(providerID 또는 InternalIP 매핑, I-2). [§6.2]"""
    return stub(
        request,
        "VM 상세(서비스 클러스터 연결 포함)",
        sources=("Nova API", "Neutron port(InternalIP 매핑 fallback)", "Magnum API"),
        ref="sample_api.md §6.2",
    )


@router.get("/clusters/{cluster}/openstack/vms/{vm_id}/metrics", summary="VM 메트릭")
async def get_openstack_vm_metrics(request: Request, cluster: str, vm_id: str):
    """VM 사용량 메트릭 — libvirt/QEMU CPU time, 메모리, 디스크/네트워크 I/O."""
    return stub(
        request,
        "VM 사용량 메트릭",
        sources=("libvirt(per-domain CPU time)", "Mimir(kepler_vm_cpu_watts)"),
    )


@router.get("/clusters/{cluster}/openstack/vms/{vm_id}/power", summary="VM 전력 귀속 [P6]")
async def get_openstack_vm_power(request: Request, cluster: str, vm_id: str):
    """VM 전력 — 물리 실측을 QEMU CPU time 비중으로 배분(host_power_attribution/attributed). 전력 계층 P6. [§6.4]"""
    return stub(
        request,
        "VM 전력 귀속(P6, attributed)",
        sources=("파생: openstack_vm_power_watts_estimated (power_attribution_plan §8)",),
        ref="sample_api.md §6.4",
    )


@router.get("/clusters/{cluster}/openstack/vms/{vm_id}/gpu-passthrough", summary="VM GPU passthrough")
async def get_openstack_vm_gpu_passthrough(request: Request, cluster: str, vm_id: str):
    """VM에 attach된 GPU/NPU passthrough — libvirt hostdev 확정 + sysfs vfio-pci 보조(SoT 우선순위 1·3)."""
    return stub(
        request,
        "VM GPU passthrough 확인(libvirt hostdev 확정 경로)",
        sources=("libvirt(hostdev)", "sysfs/PCI(vfio-pci 바인딩)", "resource-map 원장"),
        ref="design_contracts.md §3 SoT 우선순위",
    )
