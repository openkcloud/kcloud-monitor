"""
KCloud Monitor v2 — Workloads 전역 진입점 (11개, 포탈용).

클러스터를 먼저 고르지 않아도 Pod/서비스 기준으로 바로 진입하는 인덱스 API.
진입 규칙(design_contracts §6): canonical은 /clusters/{c}/workloads/* 이며,
모든 응답에 _links.self + _links.canonical 필수. 전역 목록 기본 범위는 서비스 클러스터.
service_name 파생 우선순위: app.kubernetes.io/name → k8s-app → ownerReferences[0].name → Pod 이름 prefix.
설계: sample_api.md §9.5.
"""
from fastapi import APIRouter, Depends, Request

from app.api.v2._stub import stub
from app.api.v2.deps import global_list_params

router = APIRouter()


def _pod_links(cluster: str, namespace: str, pod: str, suffix: str = "") -> dict:
    return {
        "self": f"/api/v2/workloads/pods/{cluster}/{namespace}/{pod}{suffix}",
        "canonical": f"/api/v2/clusters/{cluster}/workloads/pods/{namespace}/{pod}{suffix}",
    }


def _service_links(cluster: str, namespace: str, service_name: str, suffix: str = "") -> dict:
    # 서비스는 파생 개념(카탈로그에 클러스터 범위 경로 없음) — canonical은 해당 Pod 집합 조회.
    return {
        "self": f"/api/v2/workloads/services/{cluster}/{namespace}/{service_name}{suffix}",
        "canonical": (
            f"/api/v2/clusters/{cluster}/workloads/pods"
            f"?namespace={namespace}&service_name={service_name}"
        ),
    }


# ---------------------------------------------------------------------------
# Pods (전역)
# ---------------------------------------------------------------------------


@router.get("/workloads/pods", summary="전역 Pod 목록")
async def list_pods_global(request: Request, params: dict = Depends(global_list_params)):
    """전 클러스터 Pod 인덱스 — 기본 서비스 클러스터만, cluster 파라미터로 확장. 각 항목에 _links.canonical 포함. [§9.5]"""
    return stub(
        request,
        "전역 Pod 목록(기본 서비스 클러스터)",
        sources=("Mimir(kube_pod_info, cluster 라벨)",),
        ref="sample_api.md §9.5",
        params=params,
    )


@router.get("/workloads/pods/summary", summary="전역 Pod 집계")
async def get_pods_summary_global(request: Request):
    """전 클러스터 Pod 집계 — 클러스터별/상태별 분포."""
    return stub(request, "전역 Pod 집계", sources=("Mimir(kube_pod_status_phase)",))


@router.get("/workloads/pods/{cluster}/{namespace}/{pod}", summary="전역 Pod 상세")
async def get_pod_global(request: Request, cluster: str, namespace: str, pod: str):
    """전역 경로의 Pod 상세 — canonical 위임."""
    return stub(
        request,
        "전역 Pod 상세(canonical 위임)",
        sources=("Mimir(kube_pod_*)",),
        links=_pod_links(cluster, namespace, pod),
    )


@router.get("/workloads/pods/{cluster}/{namespace}/{pod}/power", summary="전역 Pod 전력")
async def get_pod_power_global(request: Request, cluster: str, namespace: str, pod: str):
    """전역 경로의 Pod 전력(P7) — canonical 위임."""
    return stub(
        request,
        "전역 Pod 전력(canonical 위임)",
        sources=("Mimir(kepler_pod_cpu_watts)", "파생: service_k8s_pod_power_watts_estimated"),
        links=_pod_links(cluster, namespace, pod, "/power"),
    )


@router.get("/workloads/pods/{cluster}/{namespace}/{pod}/containers", summary="전역 Pod 컨테이너")
async def list_pod_containers_global(request: Request, cluster: str, namespace: str, pod: str):
    """전역 경로의 Pod 컨테이너 목록 — canonical 위임."""
    return stub(
        request,
        "전역 Pod 컨테이너 목록(canonical 위임)",
        sources=("Mimir(kube_pod_container_info)",),
        links=_pod_links(cluster, namespace, pod, "/containers"),
    )


@router.get("/workloads/pods/{cluster}/{namespace}/{pod}/accelerators", summary="전역 Pod 가속기")
async def get_pod_accelerators_global(request: Request, cluster: str, namespace: str, pod: str):
    """전역 경로의 Pod 가속기 할당 — canonical 위임."""
    return stub(
        request,
        "전역 Pod 가속기 할당(canonical 위임)",
        sources=("resource-map 원장",),
        links=_pod_links(cluster, namespace, pod, "/accelerators"),
    )


# ---------------------------------------------------------------------------
# Services (전역 — 논리 서비스 기준 진입)
# ---------------------------------------------------------------------------


@router.get("/workloads/services", summary="전역 서비스 목록")
async def list_services_global(request: Request, params: dict = Depends(global_list_params)):
    """논리 서비스 목록 — service_name 파생 규칙으로 그룹핑. type=inference 필터 예정(추론 APM). [§9.5]"""
    return stub(
        request,
        "전역 서비스 목록(service_name 그룹)",
        sources=("Mimir(kube_pod_labels 파생)", "vllm:* (추론 서비스 KPI, 2026-07-03 실증)"),
        ref="sample_api.md §9.5",
        params=params,
    )


@router.get("/workloads/services/summary", summary="전역 서비스 집계")
async def get_services_summary_global(request: Request):
    """서비스 집계 — 서비스 수, 클러스터 분포, 상태."""
    return stub(request, "전역 서비스 집계", sources=("Mimir(kube_pod_labels 파생)",))


@router.get("/workloads/services/{cluster}/{namespace}/{service_name}", summary="서비스 상세")
async def get_service_global(request: Request, cluster: str, namespace: str, service_name: str):
    """서비스 상세 — 소속 Pod 요약, 자원/전력 합계, (추론 서비스는 vLLM KPI 예정)."""
    return stub(
        request,
        "서비스 상세",
        sources=("Mimir(kube_pod_labels 파생, vllm:*)",),
        links=_service_links(cluster, namespace, service_name),
    )


@router.get("/workloads/services/{cluster}/{namespace}/{service_name}/pods", summary="서비스 소속 Pod")
async def list_service_pods_global(
    request: Request, cluster: str, namespace: str, service_name: str
):
    """서비스에 속한 Pod 목록 — 각 항목에 canonical 링크."""
    return stub(
        request,
        "서비스 소속 Pod 목록",
        sources=("Mimir(kube_pod_labels 파생)",),
        links=_service_links(cluster, namespace, service_name, "/pods"),
    )


@router.get("/workloads/services/{cluster}/{namespace}/{service_name}/power", summary="서비스 전력 합계")
async def get_service_power_global(
    request: Request, cluster: str, namespace: str, service_name: str
):
    """서비스 전력 — 소속 Pod 전력(P7) 합계."""
    return stub(
        request,
        "서비스 전력 합계(Pod P7 합산)",
        sources=("Mimir(kepler_pod_cpu_watts)", "파생: service_k8s_pod_power_watts_estimated"),
        links=_service_links(cluster, namespace, service_name, "/power"),
    )
