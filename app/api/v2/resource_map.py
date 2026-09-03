"""가속기가 어디에 배정되어 있는지 추적하는 라우터

물리 서버에 꽂힌 가속기 카드부터 그 카드를 넘겨받은 VM, 그 VM 위에서 돌아가는
Kubernetes 노드와 Pod까지 연결 관계를 조회.

연결 관계는 PostgreSQL에 기록되며, 갱신 지연 목표는 10분 이내.
기록이 아직 연결되지 않아 모든 경로가 status="not_implemented" 반환.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from app.api.v2._stub import stub
from app.api.v2.deps import PaginationParams

router = APIRouter()

RMAP_DOC = "docs/temp/00-architecture/openkcloud_resource_mapping_architecture.md"


@router.get("/resource-map/accelerators/{acc_id}", summary="가속기 계보")
async def get_accelerator_lineage(request: Request, acc_id: str):
    """가속기 카드 한 장이 어디까지 이어지는지 조회

    - 카드가 꽂혀 있는 물리 서버
    - 카드를 넘겨받은(passthrough) VM
    - 그 VM이 참여한 Kubernetes 노드
    - 그 카드를 쓰고 있는 Pod와 컨테이너
    - 연결을 확정한 근거
    """
    return stub(
        request,
        "가속기 계보(GPU→VM→Pod 교차 추적)",
        sources=("PostgreSQL 원장", "근거: libvirt hostdev/sysfs/runtime(evidence)"),
        ref=RMAP_DOC,
    )


@router.get("/resource-map/accelerators/{acc_id}/history", summary="가속기 계보 이력")
async def get_accelerator_lineage_history(request: Request, acc_id: str):
    """가속기 카드 한 장의 배정 변경 기록 조회

    - 배정과 회수 시각
    - 그때 어느 VM 또는 Pod에 붙어 있었는지
    - 변경 사유
    """
    return stub(
        request,
        "가속기 계보 이력(할당 변경 타임라인)",
        sources=("PostgreSQL 원장(이력)",),
        ref=RMAP_DOC,
    )


@router.get("/resource-map/partitions/{partition_id}", summary="파티션 계보")
async def get_partition_lineage(request: Request, partition_id: str):
    """가속기를 나눈 파티션 한 조각이 어디에 붙어 있는지 조회

    - 이 조각이 속한 상위 가속기 카드
    - 이 조각을 쓰고 있는 워크로드
    """
    return stub(request, "파티션 계보", sources=("PostgreSQL 원장",), ref=RMAP_DOC)


@router.get("/resource-map/containers/{pod_uid}/{container}", summary="컨테이너 계보")
async def get_container_lineage(request: Request, pod_uid: str, container: str):
    """컨테이너 한 개가 어떤 가속기를 쓰는지 거꾸로 조회

    - 이 컨테이너에 배정된 가속기 카드와 파티션
    - 배정을 확정한 근거
    """
    return stub(
        request,
        "컨테이너 계보(역방향, allocated_to_container 근거 포함)",
        sources=("PostgreSQL 원장", "runtime inspect/CDI(evidence)"),
        ref=RMAP_DOC,
    )


@router.get("/resource-map/vms/{vm_uuid}", summary="VM 계보")
async def get_vm_lineage(request: Request, vm_uuid: str):
    """VM 한 대가 어디에 올라가 있고 무엇을 넘겨받았는지 조회

    - 이 VM이 올라가 있는 물리 서버
    - 넘겨받은(passthrough) 가속기 장치
    - 이 VM이 참여한 Kubernetes 노드
    """
    return stub(
        request,
        "VM 계보(물리·장치·서비스 K8s 매핑)",
        sources=("PostgreSQL 원장", "Nova/libvirt/Magnum(discovery)"),
        ref=RMAP_DOC,
    )


@router.get("/resource-map/physical-servers/{server_id}", summary="물리 서버 계보")
async def get_physical_server_lineage(request: Request, server_id: str):
    """물리 서버 한 대에 무엇이 올라가 있는지 조회

    - 이 서버에 꽂힌 가속기 카드 목록
    - VM을 올릴 수 있는 서버인지 여부
    - 이 서버에 배치된 VM과 Kubernetes 노드 목록
    """
    return stub(request, "물리 서버 계보", sources=("PostgreSQL 원장",), ref=RMAP_DOC)


@router.get("/resource-map/relationships", summary="관계 그래프 질의")
async def list_relationships(
    request: Request,
    source_type: Optional[str] = Query(None, description="연결의 시작 자원 종류: accelerator | vm | pod 등"),
    relation: Optional[str] = Query(None, description="연결 종류: attached_to | runs_on 등"),
    params: PaginationParams = Depends(),
):
    """자원 사이의 연결 관계를 조건으로 검색

    - 연결의 시작 자원과 끝 자원
    - 연결 종류(attached_to, runs_on 등)
    - 연결이 기록된 시각

    필터: source_type(시작 자원 종류), relation(연결 종류)
    """
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
    """연결 관계를 지금 즉시 다시 수집하도록 요청

    - 접수되면 바로 202 반환하고 수집은 뒤에서 진행
    - OpenStack, 가상화 계층, 서버 하드웨어, Kubernetes를 차례로 다시 훑음
    """
    return stub(
        request,
        "Discovery 수동 트리거(비동기 스캔 작업 시작)",
        sources=("Discovery 수집기(Nova/libvirt/sysfs/K8s API)",),
        ref=RMAP_DOC,
    )
