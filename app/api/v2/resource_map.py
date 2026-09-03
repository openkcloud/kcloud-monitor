"""
KCloud Monitor v2 — Resource-Map / Discovery (8개, Monitor 소유).

자원 계보 원장: 물리 GPU/NPU → (passthrough) VM → Magnum 서비스 K8s → Pod/Container 연계.
저장소는 PostgreSQL 원장(메트릭 라벨은 조회 차원일 뿐, 원장이 SoT).
attachment 확정 근거 우선순위(design_contracts §3): libvirt hostdev > Placement(OFF 확정) > sysfs > guest > runtime.
갱신 계약: resource-map 갱신 지연 ≤ 5분, RPO ≤ 5분.
설계: openkcloud_resource_mapping_architecture.md, sample_api.md §7(교차 자원 추적).
Metering/Alerter/Healer가 service-to-service JWT로 호출하는 내부 계약 API이기도 하다.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from app.api.v2._stub import stub
from app.api.v2.deps import PaginationParams

router = APIRouter()

RMAP_DOC = "docs/temp/00-architecture/openkcloud_resource_mapping_architecture.md"


@router.get("/resource-map/accelerators/{acc_id}", summary="가속기 계보")
async def get_accelerator_lineage(request: Request, acc_id: str):
    """가속기(UUID) 기준 계보 — 물리 서버→VM(passthrough)→서비스 K8s 노드→Pod까지 추적. [§7]"""
    return stub(
        request,
        "가속기 계보(GPU→VM→Pod 교차 추적)",
        sources=("PostgreSQL 원장", "근거: libvirt hostdev/sysfs/runtime(evidence)"),
        ref=f"{RMAP_DOC} + sample_api.md §7",
    )


@router.get("/resource-map/accelerators/{acc_id}/history", summary="가속기 계보 이력")
async def get_accelerator_lineage_history(request: Request, acc_id: str):
    """가속기 할당 이력 — attach/detach 이벤트 타임라인(원장 이력 테이블)."""
    return stub(
        request,
        "가속기 계보 이력(할당 변경 타임라인)",
        sources=("PostgreSQL 원장(이력)",),
        ref=RMAP_DOC,
    )


@router.get("/resource-map/partitions/{partition_id}", summary="파티션 계보")
async def get_partition_lineage(request: Request, partition_id: str):
    """파티션(MIG/vGPU/NPU slice) 계보 — 상위 가속기와 할당 워크로드."""
    return stub(request, "파티션 계보", sources=("PostgreSQL 원장",), ref=RMAP_DOC)


@router.get("/resource-map/containers/{pod_uid}/{container}", summary="컨테이너 계보")
async def get_container_lineage(request: Request, pod_uid: str, container: str):
    """컨테이너 기준 역방향 계보 — 할당 가속기/파티션과 확정 근거(runtime/CDI/DCGM PID)."""
    return stub(
        request,
        "컨테이너 계보(역방향, allocated_to_container 근거 포함)",
        sources=("PostgreSQL 원장", "runtime inspect/CDI(evidence)"),
        ref=RMAP_DOC,
    )


@router.get("/resource-map/vms/{vm_uuid}", summary="VM 계보")
async def get_vm_lineage(request: Request, vm_uuid: str):
    """VM 기준 계보 — 하이퍼바이저(물리), passthrough 장치, 서비스 K8s 노드 매핑(providerID/InternalIP)."""
    return stub(
        request,
        "VM 계보(물리·장치·서비스 K8s 매핑)",
        sources=("PostgreSQL 원장", "Nova/libvirt/Magnum(discovery)"),
        ref=RMAP_DOC,
    )


@router.get("/resource-map/physical-servers/{server_id}", summary="물리 서버 계보")
async def get_physical_server_lineage(request: Request, server_id: str):
    """물리 서버 기준 계보 — 장착 가속기, 하이퍼바이저 여부, 배치 VM/노드."""
    return stub(request, "물리 서버 계보", sources=("PostgreSQL 원장",), ref=RMAP_DOC)


@router.get("/resource-map/relationships", summary="관계 그래프 질의")
async def list_relationships(
    request: Request,
    source_type: Optional[str] = Query(None, description="시작 노드 유형(accelerator|vm|pod|...)"),
    relation: Optional[str] = Query(None, description="관계 유형(attached_to|runs_on|...)"),
    params: PaginationParams = Depends(),
):
    """원장 edge 질의 — 자원 간 관계(그래프)를 조건으로 조회."""
    merged = {k: v for k, v in vars(params).items() if v is not None}
    if source_type:
        merged["source_type"] = source_type
    if relation:
        merged["relation"] = relation
    return stub(
        request,
        "자원 관계 그래프 질의(원장 edge)",
        sources=("PostgreSQL 원장(edge 테이블)",),
        ref=RMAP_DOC,
        params=merged,
    )


@router.post("/resource-map/discovery/trigger", summary="Discovery 수동 실행", status_code=202)
async def trigger_discovery(request: Request):
    """Discovery 수집기 수동 스캔 트리거 — Nova/libvirt/sysfs/K8s API 재수집 후 원장 갱신. 비동기(202)."""
    return stub(
        request,
        "Discovery 수동 트리거(비동기 스캔 작업 시작)",
        sources=("Discovery 수집기(Nova/libvirt/sysfs/K8s API)",),
        ref=RMAP_DOC,
    )
