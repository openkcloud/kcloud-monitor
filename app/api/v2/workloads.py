"""
KCloud Monitor v2 — Workloads, 클러스터 범위 canonical (11개) + 별칭 2개.

Pod/Container/Namespace 워크로드 모니터링. canonical 경로는 /clusters/{c}/workloads/*
(전역 진입점 /workloads/* 는 workloads_global.py — design_contracts §6 진입 규칙).
데이터소스(구현 예정): Mimir(kube-state-metrics, cAdvisor, kepler), resource-map 원장.
설계: sample_api.md §9.1~§9.4 / 전력 계층 P7(Pod 귀속).
"""
from fastapi import APIRouter, Depends, Request

from app.api.v2._stub import stub
from app.api.v2.deps import list_params

router = APIRouter()

# ---------------------------------------------------------------------------
# Pods
# ---------------------------------------------------------------------------


@router.get("/clusters/{cluster}/workloads/pods", summary="Pod 목록")
async def list_pods(request: Request, cluster: str, params: dict = Depends(list_params)):
    """Pod 목록 — 상태/워크로드 유형/서비스명(service_name 파생 규칙) 필터 지원. [§9.1]"""
    return stub(
        request,
        "Pod 목록(라벨 필터·service_name 파생)",
        sources=("Mimir(kube_pod_info, kube_pod_status_phase)",),
        ref="sample_api.md §9.1",
        params=params,
    )


@router.get("/clusters/{cluster}/workloads/pods/summary", summary="Pod 집계 요약")
async def get_pods_summary(request: Request, cluster: str):
    """Pod 집계 — phase별 수, 네임스페이스 분포, 가속기 사용 Pod 수."""
    return stub(request, "Pod 집계 요약", sources=("Mimir(kube_pod_status_phase)",))


@router.get("/clusters/{cluster}/workloads/pods/{namespace}/{pod}", summary="Pod 상세")
async def get_pod(request: Request, cluster: str, namespace: str, pod: str):
    """단일 Pod 상세 — 컨테이너 구성, 자원 요청/제한, 배치 노드, 가속기 할당."""
    return stub(
        request,
        "Pod 상세(컨테이너·자원·가속기 할당)",
        sources=("Mimir(kube_pod_*, cAdvisor)", "resource-map 원장"),
    )


@router.get("/clusters/{cluster}/workloads/pods/{namespace}/{pod}/power", summary="Pod 전력 귀속 [P7]")
async def get_pod_power(request: Request, cluster: str, namespace: str, pod: str):
    """Pod 전력 — 관리 K8s는 Kepler 귀속, 서비스 K8s는 VM 전력 재배분(vm_power_split). 전력 계층 P7. [§9.2]"""
    return stub(
        request,
        "Pod 전력 귀속(P7, attributed)",
        sources=(
            "Mimir(kepler_pod_cpu_watts)",
            "파생: service_k8s_pod_power_watts_estimated(power_attribution_plan §8)",
        ),
        ref="sample_api.md §9.2",
    )


@router.get("/clusters/{cluster}/workloads/pods/{namespace}/{pod}/containers", summary="Pod 컨테이너 목록")
async def list_pod_containers(request: Request, cluster: str, namespace: str, pod: str):
    """Pod 내 컨테이너 목록 — 이미지, 상태, 재시작 수. pod_uid는 응답 본문에 포함."""
    return stub(
        request,
        "Pod 컨테이너 목록",
        sources=("Mimir(kube_pod_container_info, kube_pod_container_status_*)",),
    )


@router.get(
    "/clusters/{cluster}/workloads/pods/{namespace}/{pod}/containers/{container_name}/metrics",
    summary="컨테이너 메트릭",
)
async def get_container_metrics(
    request: Request, cluster: str, namespace: str, pod: str, container_name: str
):
    """컨테이너 메트릭 — CPU/메모리/파일시스템/네트워크(cAdvisor). K8s 표준 경로 패턴. [§9.3]"""
    return stub(
        request,
        "컨테이너 메트릭(cAdvisor)",
        sources=("Mimir(container_cpu_usage_seconds_total, container_memory_working_set_bytes)",),
        ref="sample_api.md §9.3",
    )


@router.get("/clusters/{cluster}/workloads/pods/{namespace}/{pod}/accelerators", summary="Pod 가속기 할당")
async def get_pod_accelerators(request: Request, cluster: str, namespace: str, pod: str):
    """Pod에 할당된 가속기/파티션 — runtime/CDI/DCGM PID 매핑 근거(SoT 우선순위 5)."""
    return stub(
        request,
        "Pod 가속기 할당(컨테이너 확정 근거 포함)",
        sources=("resource-map 원장", "Mimir(DCGM_* exported_pod 라벨)"),
        ref="design_contracts.md §3 SoT 우선순위",
    )


# ---------------------------------------------------------------------------
# Containers (클러스터 전역 컨테이너 뷰)
# ---------------------------------------------------------------------------


@router.get("/clusters/{cluster}/workloads/containers", summary="컨테이너 목록")
async def list_containers(request: Request, cluster: str, params: dict = Depends(list_params)):
    """클러스터 전체 컨테이너 목록 — Pod 소속, 상태, 자원 사용."""
    return stub(
        request,
        "컨테이너 목록",
        sources=("Mimir(kube_pod_container_info, cAdvisor)",),
        params=params,
    )


@router.get("/clusters/{cluster}/workloads/containers/{container_id}", summary="컨테이너 상세")
async def get_container(request: Request, cluster: str, container_id: str):
    """단일 컨테이너 상세 — 식별자 기반 직접 조회."""
    return stub(request, "컨테이너 상세", sources=("Mimir(cAdvisor)", "resource-map 원장"))


# ---------------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------------


@router.get("/clusters/{cluster}/namespaces", summary="네임스페이스 목록")
async def list_namespaces(request: Request, cluster: str, params: dict = Depends(list_params)):
    """네임스페이스 목록 — Pod 수, 자원 사용 요약."""
    return stub(request, "네임스페이스 목록", sources=("Mimir(kube_namespace_*)",), params=params)


@router.get("/clusters/{cluster}/namespaces/{namespace}/summary", summary="네임스페이스 요약")
async def get_namespace_summary(request: Request, cluster: str, namespace: str):
    """네임스페이스 요약 — Pod/컨테이너 수, CPU/메모리/가속기/전력 집계. [§9.4]"""
    return stub(
        request,
        "네임스페이스 자원·전력 요약",
        sources=("Mimir(kube_*, kepler_pod_cpu_watts)",),
        ref="sample_api.md §9.4",
    )


# ---------------------------------------------------------------------------
# 별칭(단축 경로) — 카탈로그 카운트 미포함, canonical로 연결
# ---------------------------------------------------------------------------


@router.get("/clusters/{cluster}/pods/{namespace}/{pod}", summary="Pod 상세(단축 경로)")
async def get_pod_alias(request: Request, cluster: str, namespace: str, pod: str):
    """Pod 단축 경로 — canonical은 /clusters/{c}/workloads/pods/{ns}/{pod}."""
    return stub(
        request,
        "Pod 상세 별칭",
        sources=("Mimir(kube_pod_*)",),
        links={
            "self": f"/api/v2/clusters/{cluster}/pods/{namespace}/{pod}",
            "canonical": f"/api/v2/clusters/{cluster}/workloads/pods/{namespace}/{pod}",
        },
    )


@router.get("/clusters/{cluster}/containers/{container_id}", summary="컨테이너 상세(단축 경로)")
async def get_container_alias(request: Request, cluster: str, container_id: str):
    """컨테이너 단축 경로 — canonical은 /clusters/{c}/workloads/containers/{id}."""
    return stub(
        request,
        "컨테이너 상세 별칭",
        sources=("Mimir(cAdvisor)",),
        links={
            "self": f"/api/v2/clusters/{cluster}/containers/{container_id}",
            "canonical": f"/api/v2/clusters/{cluster}/workloads/containers/{container_id}",
        },
    )
