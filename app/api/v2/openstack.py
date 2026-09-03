"""OpenStack 자원 조회 라우터

관리 클러스터 전용. 물리 서버(하이퍼바이저), 그 위에 올라간 VM, 프로젝트 단위 사용량 조회.

접속 정보가 없으면 NOT_CONFIGURED, OpenStack 호출이 실패하면 UPSTREAM_ERROR 경고와 함께
HTTP 200 + status="partial" 반환.
VM 이름은 가속기 메트릭의 hostname 라벨과 같은 값이라 두 데이터를 잇는 키로 쓰임.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Request

from app.api.v2._stub import stub
from app.api.v2.deps import WorkloadFilterParams
from app.schemas.openstack import (
    HypervisorDetailData,
    HypervisorDetailResponse,
    HypervisorItem,
    HypervisorListResponse,
    OpenStackSummaryData,
    OpenStackSummaryResponse,
    ProjectDetailResponse,
    ProjectItem,
    ProjectListResponse,
    ProjectSummaryData,
    ProjectSummaryResponse,
    VMAccelerator,
    VMDetailResponse,
    VMItem,
    VMListResponse,
    VMMetricsData,
    VMMetricsResponse,
    VMPowerData,
    VMPowerResponse,
    VMSummaryData,
    VMSummaryResponse,
)
from app.services.openstack import OpenStackError, openstack_client, parse_accelerator_alias
from app.services.vm_metrics import vm_attributed_power, vm_usage

router = APIRouter()


async def _collect_vms() -> tuple[list[VMItem], list[str]]:
    """Nova servers + flavors → VMItem 목록. (items, warnings) 반환."""
    if not openstack_client.nova_configured:
        return [], ["NOT_CONFIGURED"]
    try:
        flavor_specs = await openstack_client.flavors()
        servers = await openstack_client.servers()
    except OpenStackError as exc:
        code = str(exc) if str(exc) in ("NOT_CONFIGURED", "UPSTREAM_ERROR") else "UPSTREAM_ERROR"
        return [], [code]

    items: list[VMItem] = []
    for s in servers:
        flavor_name = (s.get("flavor") or {}).get("original_name") or (s.get("flavor") or {}).get("id")
        acc = None
        if flavor_name and flavor_name in flavor_specs:
            parsed = parse_accelerator_alias(flavor_specs[flavor_name])
            if parsed:
                acc = VMAccelerator(alias=parsed[0], count=parsed[1])
        items.append(
            VMItem(
                vm_id=s.get("id", ""),
                name=s.get("name", ""),
                host=s.get("OS-EXT-SRV-ATTR:host"),
                status=s.get("status"),
                flavor=flavor_name,
                project_id=s.get("tenant_id"),
                accelerator=acc,
            )
        )
    return items, []


@router.get("/openstack/summary", summary="OpenStack 전체 현황",
            response_model=OpenStackSummaryResponse)
async def get_openstack_summary(request: Request):
    """OpenStack 전체 규모를 한눈에 보는 요약 조회

    - hypervisor_count : 물리 서버 개수
    - vm_count : 전체 VM 개수
    - accelerator_vm_count : 가속기를 넘겨받은 VM 개수
    """
    vms, warnings = await _collect_vms()
    hv_count = 0
    if not warnings:
        try:
            hv_count = len(await openstack_client.hypervisors())
        except OpenStackError:
            warnings.append("UPSTREAM_ERROR")
    data = OpenStackSummaryData(
        hypervisor_count=hv_count,
        vm_count=len(vms),
        accelerator_vm_count=sum(1 for v in vms if v.accelerator is not None),
    )
    status = "partial" if warnings else "success"
    return OpenStackSummaryResponse(status=status, data=data, warnings=warnings)


@router.get("/openstack/hypervisors", summary="하이퍼바이저 목록",
            response_model=HypervisorListResponse)
async def list_openstack_hypervisors(request: Request,params: WorkloadFilterParams = Depends()):
    """VM을 올려 돌리는 물리 서버 목록 조회

    - hostname : 물리 서버 호스트명
    - state : 서버 연결 상태(up | down)
    - status : 운영 상태(enabled | disabled)
    - vm_count : 이 서버에 올라간 VM 개수
    """
    if not openstack_client.nova_configured:
        return HypervisorListResponse(status="partial", data=[], warnings=["NOT_CONFIGURED"])
    try:
        hvs = await openstack_client.hypervisors()
        servers = await openstack_client.servers()
    except OpenStackError as exc:
        code = str(exc) if str(exc) in ("NOT_CONFIGURED", "UPSTREAM_ERROR") else "UPSTREAM_ERROR"
        return HypervisorListResponse(status="partial", data=[], warnings=[code])

    vm_by_host: dict[str, int] = {}
    for s in servers:
        h = s.get("OS-EXT-SRV-ATTR:host")
        if h:
            vm_by_host[h] = vm_by_host.get(h, 0) + 1

    items = [
        HypervisorItem(
            hostname=h.get("hypervisor_hostname", ""),
            state=h.get("state"),
            status=h.get("status"),
            vm_count=vm_by_host.get(h.get("hypervisor_hostname", ""), 0),
        )
        for h in hvs
    ]
    warnings = [] if items else ["NO_DATA"]
    return HypervisorListResponse(status="success" if items else "partial", data=items, warnings=warnings)


@router.get("/openstack/hypervisors/{host}/vms", summary="하이퍼바이저별 VM 배치",
            response_model=VMListResponse)
async def list_hypervisor_vms(request: Request,host: str):
    """물리 서버 한 대에 올라간 VM 목록 조회

    - VM ID, 이름, 상태(ACTIVE | SHUTOFF 등)
    - flavor 이름, 소속 프로젝트
    - 넘겨받은 가속기 종류와 개수
    """
    vms, warnings = await _collect_vms()
    filtered = [v for v in vms if v.host == host]
    if warnings:
        return VMListResponse(status="partial", data=[], warnings=warnings)
    return VMListResponse(status="success" if filtered else "partial",
                          data=filtered, warnings=[] if filtered else ["NO_DATA"])


@router.get("/openstack/vms", summary="VM 목록", response_model=VMListResponse)
async def list_openstack_vms(request: Request,params: WorkloadFilterParams = Depends()):
    """전체 VM 목록 조회

    - VM ID, 이름, 상태(ACTIVE | SHUTOFF 등)
    - 이 VM이 올라간 물리 서버
    - flavor 이름, 소속 프로젝트
    - 넘겨받은 가속기 종류와 개수 (없으면 null)
    """
    vms, warnings = await _collect_vms()
    status = "partial" if warnings else "success"
    return VMListResponse(status=status, data=vms, warnings=warnings)


@router.get("/openstack/vms/summary", summary="VM 집계 요약",
            response_model=VMSummaryResponse)
async def get_openstack_vms_summary(request: Request):
    """VM 전체를 여러 기준으로 세어본 집계값 조회

    - total : 전체 VM 개수
    - by_status : 상태별(ACTIVE, SHUTOFF 등) VM 개수
    - accelerator_vm_count : 가속기를 넘겨받은 VM 개수
    - by_project : 프로젝트별 VM 개수
    """
    vms, warnings = await _collect_vms()
    by_status: dict[str, int] = {}
    by_project: dict[str, int] = {}
    accelerator_vm_count = 0
    for v in vms:
        if v.status:
            by_status[v.status] = by_status.get(v.status, 0) + 1
        if v.project_id:
            by_project[v.project_id] = by_project.get(v.project_id, 0) + 1
        if v.accelerator is not None:
            accelerator_vm_count += 1

    data = VMSummaryData(
        total=len(vms),
        by_status=by_status,
        accelerator_vm_count=accelerator_vm_count,
        by_project=by_project,
    )
    status = "partial" if warnings else "success"
    return VMSummaryResponse(status=status, data=data, warnings=warnings)


@router.get("/openstack/vms/{vm_id}", summary="VM 상세", response_model=VMDetailResponse)
async def get_openstack_vm(request: Request,vm_id: str):
    """VM 한 대의 상세 조회

    - VM ID, 이름, 상태(ACTIVE | SHUTOFF 등)
    - 이 VM이 올라간 물리 서버
    - flavor 이름, 소속 프로젝트
    - 넘겨받은 가속기 종류와 개수

    vm_id 자리에 VM ID 또는 VM 이름 둘 다 사용 가능.
    """
    vms, warnings = await _collect_vms()
    if warnings:
        return VMDetailResponse(status="partial", data=None, warnings=warnings)
    match = next((v for v in vms if v.vm_id == vm_id or v.name == vm_id), None)
    if match is None:
        return VMDetailResponse(status="partial", data=None, warnings=["NOT_FOUND"])
    return VMDetailResponse(status="success", data=match, warnings=[])


# ── 실구현: Keystone 프로젝트 / 하이퍼바이저 상세 / VM 집계 ──────────────────

@router.get("/openstack/projects", summary="프로젝트 목록",
            response_model=ProjectListResponse)
async def list_openstack_projects(request: Request,params: WorkloadFilterParams = Depends()):
    """자원을 나눠 쓰는 단위인 프로젝트 목록 조회

    - project_id : 프로젝트 ID
    - name : 프로젝트 이름
    - vm_count : 소속 VM 개수
    - accelerator_vm_count : 가속기를 넘겨받은 VM 개수
    """
    if not openstack_client.configured:
        return ProjectListResponse(status="partial", data=[], warnings=["NOT_CONFIGURED"])
    try:
        projects = await openstack_client.keystone_projects()
    except OpenStackError as exc:
        code = str(exc) if str(exc) in ("NOT_CONFIGURED", "UPSTREAM_ERROR") else "UPSTREAM_ERROR"
        return ProjectListResponse(status="partial", data=[], warnings=[code])

    vms, vm_warnings = await _collect_vms()
    vm_count_by_project: dict[str, int] = {}
    acc_count_by_project: dict[str, int] = {}
    for v in vms:
        if not v.project_id:
            continue
        vm_count_by_project[v.project_id] = vm_count_by_project.get(v.project_id, 0) + 1
        if v.accelerator is not None:
            acc_count_by_project[v.project_id] = acc_count_by_project.get(v.project_id, 0) + 1

    items = [
        ProjectItem(
            project_id=pid,
            name=name,
            vm_count=vm_count_by_project.get(pid, 0),
            accelerator_vm_count=acc_count_by_project.get(pid, 0),
        )
        for pid, name in projects.items()
    ]
    if params.search:
        q = params.search.lower()
        items = [i for i in items if q in i.name.lower()]

    status = "partial" if vm_warnings else "success"
    return ProjectListResponse(status=status, data=items, warnings=vm_warnings)


@router.get("/openstack/projects/{project_id}", summary="프로젝트 상세",
            response_model=ProjectDetailResponse)
async def get_openstack_project(request: Request,project_id: str):
    """프로젝트 한 개의 상세 조회

    - project_id : 프로젝트 ID
    - name : 프로젝트 이름
    - vm_count : 소속 VM 개수
    - accelerator_vm_count : 가속기를 넘겨받은 VM 개수
    """
    if not openstack_client.configured:
        return ProjectDetailResponse(status="partial", data=None, warnings=["NOT_CONFIGURED"])
    try:
        projects = await openstack_client.keystone_projects()
    except OpenStackError as exc:
        code = str(exc) if str(exc) in ("NOT_CONFIGURED", "UPSTREAM_ERROR") else "UPSTREAM_ERROR"
        return ProjectDetailResponse(status="partial", data=None, warnings=[code])

    name = projects.get(project_id)
    if name is None:
        return ProjectDetailResponse(status="partial", data=None, warnings=["NOT_FOUND"])

    vms, vm_warnings = await _collect_vms()
    project_vms = [v for v in vms if v.project_id == project_id]
    data = ProjectItem(
        project_id=project_id,
        name=name,
        vm_count=len(project_vms),
        accelerator_vm_count=sum(1 for v in project_vms if v.accelerator is not None),
    )
    status = "partial" if vm_warnings else "success"
    return ProjectDetailResponse(status=status, data=data, warnings=vm_warnings)


@router.get("/openstack/projects/{project_id}/summary", summary="프로젝트 자원 요약",
            response_model=ProjectSummaryResponse)
async def get_openstack_project_summary(request: Request,project_id: str):
    """프로젝트 한 개가 쓰는 자원 요약 조회

    - project_id, name : 프로젝트 ID와 이름
    - vm_count : 소속 VM 개수
    - accelerator_vm_count : 가속기를 넘겨받은 VM 개수
    - total_vcpus, total_ram_mb : vCPU와 메모리 합계. flavor 상세를 가져올 수 없어 현재 null
    """
    if not openstack_client.configured:
        return ProjectSummaryResponse(status="partial", data=None, warnings=["NOT_CONFIGURED"])
    try:
        projects = await openstack_client.keystone_projects()
    except OpenStackError as exc:
        code = str(exc) if str(exc) in ("NOT_CONFIGURED", "UPSTREAM_ERROR") else "UPSTREAM_ERROR"
        return ProjectSummaryResponse(status="partial", data=None, warnings=[code])

    name = projects.get(project_id)
    if name is None:
        return ProjectSummaryResponse(status="partial", data=None, warnings=["NOT_FOUND"])

    vms, vm_warnings = await _collect_vms()
    project_vms = [v for v in vms if v.project_id == project_id]
    data = ProjectSummaryData(
        project_id=project_id,
        name=name,
        vm_count=len(project_vms),
        accelerator_vm_count=sum(1 for v in project_vms if v.accelerator is not None),
    )
    status = "partial" if vm_warnings else "success"
    return ProjectSummaryResponse(status=status, data=data, warnings=vm_warnings)


@router.get("/openstack/hypervisors/{host}", summary="하이퍼바이저 상세",
            response_model=HypervisorDetailResponse)
async def get_openstack_hypervisor(request: Request,host: str):
    """물리 서버 한 대의 상세 조회

    - hostname, state, status : 호스트명, 연결 상태, 운영 상태
    - vcpus, vcpus_used : 전체 vCPU 수와 배정된 vCPU 수
    - memory_mb, memory_mb_used : 전체 메모리와 배정된 메모리(MB)
    - local_gb, local_gb_used : 전체 로컬 디스크와 사용량(GB)
    - running_vms : 실행 중인 VM 개수
    - hypervisor_type, hypervisor_version, host_ip : 가상화 종류, 버전, 관리 IP
    - vms : 이 서버에 올라간 VM 목록
    """
    if not openstack_client.nova_configured:
        return HypervisorDetailResponse(status="partial", data=None, warnings=["NOT_CONFIGURED"])
    try:
        hvs = await openstack_client.hypervisors()
    except OpenStackError as exc:
        code = str(exc) if str(exc) in ("NOT_CONFIGURED", "UPSTREAM_ERROR") else "UPSTREAM_ERROR"
        return HypervisorDetailResponse(status="partial", data=None, warnings=[code])

    match = next((h for h in hvs if h.get("hypervisor_hostname") == host), None)
    if match is None:
        return HypervisorDetailResponse(status="partial", data=None, warnings=["NOT_FOUND"])

    vms, vm_warnings = await _collect_vms()
    placed = [v for v in vms if v.host == host]
    data = HypervisorDetailData(
        hostname=match.get("hypervisor_hostname", ""),
        state=match.get("state"),
        status=match.get("status"),
        vcpus=match.get("vcpus"),
        vcpus_used=match.get("vcpus_used"),
        memory_mb=match.get("memory_mb"),
        memory_mb_used=match.get("memory_mb_used"),
        local_gb=match.get("local_gb"),
        local_gb_used=match.get("local_gb_used"),
        running_vms=match.get("running_vms"),
        hypervisor_type=match.get("hypervisor_type"),
        hypervisor_version=match.get("hypervisor_version"),
        host_ip=match.get("host_ip"),
        vms=placed,
    )
    status = "partial" if vm_warnings else "success"
    return HypervisorDetailResponse(status=status, data=data, warnings=vm_warnings)


# ── 실구현: VM 사용량·전력 귀속 (libvirt exporter) ──────────────────────────

async def _resolve_vm(vm_id: str) -> tuple[Optional[VMItem], list[str]]:
    """vm_id(uuid 또는 name)를 nova에서 실 VMItem으로 해소. (match, warnings) 반환.

    libvirt/IPMI 쿼리에 넣는 uuid·host는 반드시 여기서 해소된 nova 출처 값을 쓴다
    (경로 파라미터 원문을 PromQL에 직접 넣지 않기 위함 — 인젝션 방지).
    """
    vms, warnings = await _collect_vms()
    if warnings:
        return None, warnings
    match = next((v for v in vms if v.vm_id == vm_id or v.name == vm_id), None)
    if match is None:
        return None, ["NOT_FOUND"]
    return match, []


@router.get("/openstack/vms/{vm_id}/metrics", summary="VM 메트릭",
            response_model=VMMetricsResponse)
async def get_openstack_vm_metrics(request: Request,vm_id: str):
    """VM 한 대가 실제로 쓰고 있는 사용량 조회

    - cpu : 사용 중 vCPU 코어 수, 누적 CPU 시간
    - memory : 호스트가 실제로 내준 메모리, 게스트가 인식하는 메모리, 미사용 메모리(bytes)
    - disk : 누적 읽기, 쓰기 바이트
    - network : 누적 수신, 송신 바이트
    """
    match, warnings = await _resolve_vm(vm_id)
    if match is None:
        return VMMetricsResponse(status="partial", data=None, warnings=warnings)

    r = await vm_usage(match.vm_id)
    data = None
    if r["data"] is not None:
        data = VMMetricsData(vm_id=match.vm_id, name=match.name, host=match.host, **r["data"])
    return VMMetricsResponse(status=r["status"], data=data, warnings=r["warnings"])


@router.get("/openstack/vms/{vm_id}/power", summary="VM 전력 귀속",
            response_model=VMPowerResponse)
async def get_openstack_vm_power(request: Request,vm_id: str):
    """VM 한 대가 쓴 것으로 볼 수 있는 전력 조회

    - attributed_watts : 이 VM에 배분된 전력(W)
    - server_total_watts : 이 VM이 올라간 물리 서버의 총 전력(W)
    - cpu_share_pct : 그 서버 안에서 이 VM이 차지한 CPU 비율(%)
    - method : 배분 방식

    VM 단위 전력계가 없으므로 서버 총 전력을 CPU 점유 비율로 나눈 추정치.
    """
    match, warnings = await _resolve_vm(vm_id)
    if match is None:
        return VMPowerResponse(status="partial", data=None, warnings=warnings)

    r = await vm_attributed_power(match.vm_id, match.host)
    data = None
    if r["data"] is not None:
        data = VMPowerData(vm_id=match.vm_id, name=match.name, host=match.host, **r["data"])
    return VMPowerResponse(status=r["status"], data=data, warnings=r["warnings"])


# ── 미구현(libvirt hostdev 필요) — stub 유지 ─────────────────────────────

@router.get("/openstack/vms/{vm_id}/gpu-passthrough", summary="VM GPU passthrough")
async def get_openstack_vm_gpu_passthrough(request: Request,vm_id: str):
    """VM 한 대가 물리 가속기를 직접 넘겨받았는지 조회

    - 넘겨받은 가속기 장치의 PCI 주소와 종류
    - 가상화 계층에서 확인한 장치 정보를 아직 걷어오지 않아 status="not_implemented" 반환
    """
    return stub(request, "VM GPU passthrough 확인(libvirt hostdev)", sources=("libvirt(hostdev)",))
