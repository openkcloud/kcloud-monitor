"""
KCloud Monitor v2 — Clusters (5개).

최상위 진입점: 모든 자원 탐색의 시작. 관리(mgmt)/서비스(service) 클러스터를 모두 다룬다.
데이터소스(구현 예정): Mimir(PromQL), kube-state-metrics, K8s API, 클러스터 레지스트리.
설계: sample_api.md §1.2, §2.1, §2.2 / 전력 계층 P1(sfr_api_mapping OPT.002).
"""
from fastapi import APIRouter, Depends, Request

from app.api.v2._stub import stub
from app.api.v2.deps import list_params

router = APIRouter()


@router.get("/clusters", summary="클러스터 목록")
async def list_clusters(request: Request, params: dict = Depends(list_params)):
    """등록된 전체 클러스터 목록 — 유형(management/service), 상태, 노드/가속기 수 요약. [§1.2]"""
    return stub(
        request,
        "클러스터 목록(유형·상태·자원 수 요약)",
        sources=("클러스터 레지스트리", "Mimir(PromQL)", "kube-state-metrics"),
        ref="sample_api.md §1.2",
        params=params,
    )


@router.get("/clusters/{cluster}", summary="클러스터 상세")
async def get_cluster(request: Request, cluster: str):
    """단일 클러스터 기본 정보 — 유형, K8s 버전, API 엔드포인트, 소속 노드 수."""
    return stub(
        request,
        "클러스터 상세(유형·버전·구성)",
        sources=("클러스터 레지스트리", "K8s API"),
    )


@router.get("/clusters/{cluster}/summary", summary="클러스터 리소스 요약")
async def get_cluster_summary(request: Request, cluster: str):
    """클러스터 KPI 요약 — 노드/가속기/워크로드 수·상태, 전력 합계, 사용률 집계. [§2.1]"""
    return stub(
        request,
        "클러스터 리소스 요약(노드·가속기·워크로드·전력 KPI)",
        sources=("Mimir(PromQL)", "kube-state-metrics"),
        ref="sample_api.md §2.1",
    )


@router.get("/clusters/{cluster}/topology", summary="클러스터 토폴로지")
async def get_cluster_topology(request: Request, cluster: str):
    """계층형 토폴로지 — Node → Pod → Container → GPU/NPU 트리. 무거운 집계(P95 ≤ 5초 목표). [§2.2]"""
    return stub(
        request,
        "계층형 토폴로지(Node→Pod→Container→가속기)",
        sources=("Mimir(PromQL)", "kube-state-metrics", "resource-map 원장"),
        ref="sample_api.md §2.2",
    )


@router.get("/clusters/{cluster}/power", summary="클러스터 전력 합계 [P1]")
async def get_cluster_power(request: Request, cluster: str):
    """클러스터 전력 합계 — 노드(Kepler/IPMI)·가속기(DCGM) 전력 집계. 전력 계층 P1."""
    return stub(
        request,
        "클러스터 전력 합계(P1)",
        sources=("Mimir(kepler_node_cpu_watts)", "Mimir(ipmi_* BMC)", "Mimir(DCGM_FI_DEV_POWER_USAGE)"),
        ref="sfr_api_mapping.md OPT.002 P1",
    )
