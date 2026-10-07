"""클러스터를 고르지 않고 워크로드 전체를 보는 라우터

어느 클러스터에 있는지 몰라도 Pod와 서비스를 바로 찾을 수 있는 경로.
모든 응답에 `_links.canonical` 로 클러스터를 포함한 정식 경로를 함께 안내.
"""
import asyncio
from types import SimpleNamespace
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from app.api.v2.deps import WorkloadFilterParams
from app.api.v2.workloads import (
    fetch_pod_accelerators,
    fetch_pod_containers,
    fetch_pod_detail,
    fetch_pod_power,
    fetch_pods,
    fetch_pods_summary,
)
from app.schemas.workloads import (
    ContainerListResponse,
    PodAcceleratorResponse,
    PodDetailResponse,
    PodListResponse,
    PodPowerResponse,
    PodSummaryData,
    PodSummaryResponse,
    ServiceDetailData,
    ServiceDetailResponse,
    ServiceItem,
    ServiceListResponse,
    ServicePowerData,
    ServicePowerResponse,
    ServiceSummaryData,
)
from app.services.cluster_discovery import cluster_discovery

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pod_links(cluster: str, namespace: str, pod: str, suffix: str = "") -> dict:
    return {
        "self": f"/api/v2/workloads/pods/{cluster}/{namespace}/{pod}{suffix}",
        "canonical": f"/api/v2/clusters/{cluster}/workloads/pods/{namespace}/{pod}{suffix}",
    }


def _service_links(cluster: str, namespace: str, service_name: str, suffix: str = "") -> dict:
    return {
        "self": f"/api/v2/workloads/services/{cluster}/{namespace}/{service_name}{suffix}",
        "canonical": (
            f"/api/v2/clusters/{cluster}/workloads/pods"
            f"?namespace={namespace}&service_name={service_name}"
        ),
    }


def _all_params(params: Optional[WorkloadFilterParams] = None, **overrides) -> SimpleNamespace:
    defaults = dict(
        limit=100000, offset=0, sort_by=None, sort_order="asc",
        search=None, namespace=None, service_name=None,
        workload_type=None, status=None, project=None,
    )
    if params:
        for k in defaults:
            if hasattr(params, k) and k not in ("limit", "offset"):
                defaults[k] = getattr(params, k)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def _resolve_clusters(cluster_filter: Optional[str]) -> list[str]:
    clusters = await cluster_discovery.get_clusters()
    if cluster_filter and cluster_filter in clusters:
        return [cluster_filter]
    return list(clusters.keys())


# ---------------------------------------------------------------------------
# Pods (전역)
# ---------------------------------------------------------------------------


@router.get("/workloads/pods", summary="전체 클러스터 Pod 목록", response_model=PodListResponse)
async def list_pods_global(
    request: Request,
    params: WorkloadFilterParams = Depends(),
    cluster: Optional[str] = Query(None, description="클러스터 이름 필터. 미지정 시 전체"),
):
    """모든 클러스터의 Pod를 한 목록으로 조회
    
    - 네임스페이스, Pod 이름, 소속 클러스터, 노드 이름
    - 상태(Running | Pending | Succeeded | Failed | Unknown), Pod IP, 노드 IP, 생성 시각
    - 상위 리소스 종류(예: ReplicaSet, StatefulSet, DaemonSet, Job)와 이름, 서비스 이름
    - 컨테이너 개수, 재시작 횟수
    - CPU 사용량(코어), 메모리 사용량(bytes)
    - total : 필터 적용 후 전체 Pod 개수
    
    cluster, namespace, status, workload_type, service_name 으로 필터. 없는 클러스터 이름이면 전체 클러스터 기준. search 는 Pod 이름과 네임스페이스에 적용. 정렬(sort_by)은 클러스터 안에서만 적용. Pod가 하나도 없으면 NO_DATA 경고.
    """
    names = await _resolve_clusters(cluster)
    inner = _all_params(params)

    results = await asyncio.gather(*(fetch_pods(n, inner) for n in names))

    all_pods = []
    for pods, _, _ in results:
        all_pods.extend(pods)

    total = len(all_pods)
    page = all_pods[params.offset : params.offset + params.limit]
    warnings: list[str] = [] if all_pods else ["NO_DATA"]
    return PodListResponse(
        status="success" if all_pods else "partial",
        pods=page, total=total, warnings=warnings,
    )


@router.get("/workloads/pods/summary", summary="전체 클러스터 Pod 집계", response_model=PodSummaryResponse)
async def get_pods_summary_global(request: Request):
    """모든 클러스터의 Pod 상태별 개수 합계 조회
    
    - total_count : 전체 Pod 개수
    - running_count, pending_count : Running, Pending 상태 개수
    - succeeded_count, failed_count, unknown_count : 정상 완료, 실패, 상태 불명 개수
    - namespace_distribution : 네임스페이스별 Pod 개수
    
    Pod가 하나도 없으면 NO_DATA 경고.
    """
    names = await _resolve_clusters(None)
    results = await asyncio.gather(*(fetch_pods_summary(n) for n in names))

    merged = PodSummaryData()
    for data, _ in results:
        merged.total_count += data.total_count
        merged.running_count += data.running_count
        merged.pending_count += data.pending_count
        merged.succeeded_count += data.succeeded_count
        merged.failed_count += data.failed_count
        merged.unknown_count += data.unknown_count
        for ns, cnt in data.namespace_distribution.items():
            merged.namespace_distribution[ns] = (
                merged.namespace_distribution.get(ns, 0) + cnt
            )

    warnings: list[str] = [] if merged.total_count > 0 else ["NO_DATA"]
    return PodSummaryResponse(
        status="success" if merged.total_count else "partial",
        data=merged, warnings=warnings,
    )


@router.get("/workloads/pods/{cluster}/{namespace}/{pod}", summary="Pod 상세(클러스터 지정)")
async def get_pod_global(request: Request, cluster: str, namespace: str, pod: str):
    """클러스터 이름을 경로에 넣어 Pod 한 개의 상세 조회
    
    - 네임스페이스, Pod 이름, 소속 클러스터, 고유 ID(uid), 노드 이름
    - 상태(Running | Pending | Succeeded | Failed | Unknown), Pod IP, 노드 IP, 생성 시각
    - 상위 리소스 종류와 이름, 서비스 이름, 컨테이너 개수, 재시작 횟수
    - CPU 사용량, 요청량, 상한값(코어), 메모리 사용량, 요청량, 상한값(bytes)
    - _links.self: 이번 요청 경로, _links.canonical: 클러스터를 포함한 정식 경로
    
    Pod가 없으면 data=null, NO_DATA 경고.
    """
    data, warnings = await fetch_pod_detail(cluster, namespace, pod)
    status = "success" if data else "partial"
    resp = PodDetailResponse(status=status, data=data, warnings=warnings)
    return {**resp.model_dump(), "_links": _pod_links(cluster, namespace, pod)}


@router.get(
    "/workloads/pods/{cluster}/{namespace}/{pod}/power", summary="Pod 추정 전력(클러스터 지정)",
)
async def get_pod_power_global(
    request: Request, cluster: str, namespace: str, pod: str,
):
    """클러스터 이름을 경로에 넣어 Pod 한 개의 추정 전력 조회
    
    - watts : Pod 추정 전력(W). 최근 5분 평균
    - source : 산출 근거 (kepler)
    - _links.self: 이번 요청 경로, _links.canonical: 클러스터를 포함한 정식 경로
    
    Kepler 가 컨테이너별로 추정한 전력의 합. 전력값이 없으면 data=null, NO_POWER_DATA 경고.
    """
    data, warnings = await fetch_pod_power(cluster, namespace, pod)
    status = "success" if data else "partial"
    resp = PodPowerResponse(status=status, data=data, warnings=warnings)
    return {**resp.model_dump(), "_links": _pod_links(cluster, namespace, pod, "/power")}


@router.get(
    "/workloads/pods/{cluster}/{namespace}/{pod}/containers",
    summary="Pod 컨테이너 목록(클러스터 지정)",
)
async def list_pod_containers_global(
    request: Request, cluster: str, namespace: str, pod: str,
):
    """클러스터 이름을 경로에 넣어 Pod 한 개 안의 컨테이너 목록 조회
    
    - 컨테이너 이름, 컨테이너 ID, 이미지, 상태(running | waiting | terminated), 재시작 횟수
    - 네임스페이스, Pod 이름, 소속 클러스터
    - CPU 사용량, 요청량, 상한값(코어), 메모리 사용량, 요청량, 상한값(bytes)
    - total : 컨테이너 개수
    - _links.self: 이번 요청 경로, _links.canonical: 클러스터를 포함한 정식 경로
    
    Pod 정보가 없으면 빈 목록, NO_DATA 경고.
    """
    containers, warnings = await fetch_pod_containers(cluster, namespace, pod)
    status = "success" if not warnings else "partial"
    resp = ContainerListResponse(
        status=status, containers=containers, total=len(containers), warnings=warnings,
    )
    return {
        **resp.model_dump(),
        "_links": _pod_links(cluster, namespace, pod, "/containers"),
    }


@router.get(
    "/workloads/pods/{cluster}/{namespace}/{pod}/accelerators",
    summary="Pod 할당 가속기(클러스터 지정)",
)
async def get_pod_accelerators_global(
    request: Request, cluster: str, namespace: str, pod: str,
):
    """클러스터 이름을 경로에 넣어 Pod 한 개에 배정된 가속기 조회
    
    - 가속기 ID, 벤더, 모델명
    - _links.self: 이번 요청 경로, _links.canonical: 클러스터를 포함한 정식 경로
    
    NVIDIA 클러스터만 값 있음. 그 외 클러스터이거나 배정된 카드가 없으면 빈 목록, POD_ACCELERATOR_DATA_NOT_AVAILABLE 경고.
    """
    items, warnings = await fetch_pod_accelerators(cluster, namespace, pod)
    status = "success" if not warnings else "partial"
    resp = PodAcceleratorResponse(status=status, data=items, warnings=warnings)
    return {
        **resp.model_dump(),
        "_links": _pod_links(cluster, namespace, pod, "/accelerators"),
    }


# ---------------------------------------------------------------------------
# Services (전역)
# ---------------------------------------------------------------------------


def _group_by_service(pods: list) -> dict[tuple[str, str, str], list]:
    groups: dict[tuple[str, str, str], list] = {}
    for p in pods:
        svc = p.service_name or p.pod
        groups.setdefault((p.cluster, p.namespace, svc), []).append(p)
    return groups


def _build_service_item(key: tuple[str, str, str], pods: list) -> ServiceItem:
    cluster, namespace, svc = key
    running = sum(1 for p in pods if p.phase == "Running")
    cpu_vals = [p.cpu_usage for p in pods if p.cpu_usage is not None]
    mem_vals = [p.memory_usage_bytes for p in pods if p.memory_usage_bytes is not None]
    return ServiceItem(
        service_name=svc, namespace=namespace, cluster=cluster,
        pod_count=len(pods), running_pod_count=running,
        cpu_usage=sum(cpu_vals) if cpu_vals else None,
        memory_usage_bytes=sum(mem_vals) if mem_vals else None,
    )


@router.get("/workloads/services", summary="전체 클러스터 서비스 목록", response_model=ServiceListResponse)
async def list_services_global(
    request: Request,
    params: WorkloadFilterParams = Depends(),
    cluster: Optional[str] = Query(None, description="클러스터 이름 필터. 미지정 시 전체"),
):
    """모든 클러스터의 서비스를 한 목록으로 조회
    
    - 서비스 이름, 네임스페이스, 소속 클러스터
    - pod_count, running_pod_count : 소속 Pod 개수, Running 상태 개수
    - cpu_usage, memory_usage_bytes : 소속 Pod CPU 사용량 합계(코어), 메모리 사용량 합계(bytes)
    - total : 서비스 이름 검색 적용 후 서비스 개수
    - summary : 전체 서비스 수(total_services), 클러스터별 서비스 수(cluster_distribution). 페이지와 서비스 이름 검색 적용 전 기준
    
    같은 클러스터, 네임스페이스, 서비스 이름의 Pod를 하나로 묶음. 서비스 이름을 추정할 수 없는 Pod는 Pod 이름을 서비스 이름으로 사용. cluster, namespace, status, workload_type, service_name 은 묶기 전 Pod에 적용. search 는 Pod 이름 또는 네임스페이스와 서비스 이름에 모두 적용. 서비스가 없으면 NO_DATA 경고.
    """
    names = await _resolve_clusters(cluster)
    inner = _all_params(params)
    results = await asyncio.gather(*(fetch_pods(n, inner) for n in names))

    all_pods = []
    for pods, _, _ in results:
        all_pods.extend(pods)

    groups = _group_by_service(all_pods)
    items = [_build_service_item(k, v) for k, v in groups.items()]

    cluster_dist: dict[str, int] = {}
    for item in items:
        cluster_dist[item.cluster] = cluster_dist.get(item.cluster, 0) + 1
    summary = ServiceSummaryData(total_services=len(items), cluster_distribution=cluster_dist)

    if params.search:
        q = params.search.lower()
        items = [i for i in items if q in i.service_name.lower()]

    total = len(items)
    page = items[params.offset : params.offset + params.limit]
    warnings: list[str] = [] if items else ["NO_DATA"]
    return ServiceListResponse(
        status="success" if items else "partial",
        services=page, total=total, summary=summary, warnings=warnings,
    )


@router.get(
    "/workloads/services/{cluster}/{namespace}/{service_name}",
    summary="서비스 상세",
)
async def get_service_global(
    request: Request, cluster: str, namespace: str, service_name: str,
):
    """서비스 한 개의 자원 사용 상세 조회
    
    - 서비스 이름, 네임스페이스, 소속 클러스터
    - pod_count, running_pod_count : 소속 Pod 개수, Running 상태 개수
    - cpu_usage, memory_usage_bytes : 소속 Pod CPU 사용량 합계(코어), 메모리 사용량 합계(bytes)
    - cpu_requests, memory_requests_bytes : 값 미제공 시 null
    - _links.self: 이번 요청 경로, _links.canonical: 이 서비스의 Pod를 거르는 클러스터 Pod 목록 경로
    
    서비스 이름이 부분 일치하는 Pod를 소속 Pod로 판단. 소속 Pod가 없으면 data=null, NO_DATA 경고.
    """
    inner = _all_params(namespace=namespace, service_name=service_name)
    pods, _, _ = await fetch_pods(cluster, inner)

    links = _service_links(cluster, namespace, service_name)

    if not pods:
        resp = ServiceDetailResponse(status="partial", data=None, warnings=["NO_DATA"])
        return {**resp.model_dump(), "_links": links}

    running = sum(1 for p in pods if p.phase == "Running")
    cpu_vals = [p.cpu_usage for p in pods if p.cpu_usage is not None]
    mem_vals = [p.memory_usage_bytes for p in pods if p.memory_usage_bytes is not None]

    data = ServiceDetailData(
        service_name=service_name, namespace=namespace, cluster=cluster,
        pod_count=len(pods), running_pod_count=running,
        cpu_usage=sum(cpu_vals) if cpu_vals else None,
        memory_usage_bytes=sum(mem_vals) if mem_vals else None,
    )
    resp = ServiceDetailResponse(status="success", data=data, warnings=[])
    return {**resp.model_dump(), "_links": links}


@router.get(
    "/workloads/services/{cluster}/{namespace}/{service_name}/pods",
    summary="서비스 소속 Pod 목록",
)
async def list_service_pods_global(
    request: Request, cluster: str, namespace: str, service_name: str,
):
    """서비스 한 개에 속한 Pod 목록 조회
    
    - 네임스페이스, Pod 이름, 소속 클러스터, 노드 이름, 상태(Running | Pending | Succeeded | Failed | Unknown)
    - Pod IP, 노드 IP, 생성 시각, 상위 리소스 종류(예: ReplicaSet, StatefulSet, DaemonSet, Job)와 이름, 서비스 이름
    - 컨테이너 개수, 재시작 횟수, CPU 사용량(코어), 메모리 사용량(bytes)
    - total : 이 서비스의 Pod 개수
    - _links.self: 이번 요청 경로, _links.canonical: 이 서비스의 Pod를 거르는 클러스터 Pod 목록 경로
    
    서비스 이름이 부분 일치하는 Pod를 소속 Pod로 판단. Pod 정보 미수집 시 NO_DATA 경고.
    """
    inner = _all_params(namespace=namespace, service_name=service_name)
    pods, total, warnings = await fetch_pods(cluster, inner)
    status = "success" if pods else "partial"
    resp = PodListResponse(status=status, pods=pods, total=total, warnings=warnings)
    return {
        **resp.model_dump(),
        "_links": _service_links(cluster, namespace, service_name, "/pods"),
    }


@router.get(
    "/workloads/services/{cluster}/{namespace}/{service_name}/power",
    summary="서비스 추정 전력 합계",
)
async def get_service_power_global(
    request: Request, cluster: str, namespace: str, service_name: str,
):
    """서비스 한 개의 추정 전력 합계 조회
    
    - watts : 소속 Pod 추정 전력 합계(W)
    - source : 산출 근거 (kepler)
    - _links.self: 이번 요청 경로, _links.canonical: 이 서비스의 Pod를 거르는 클러스터 Pod 목록 경로
    
    Kepler 가 컨테이너별로 추정한 전력을 소속 Pod 전체에 대해 더한 값. 어느 Pod도 전력값이 없으면 data=null, NO_POWER_DATA 경고.
    """
    inner = _all_params(namespace=namespace, service_name=service_name)
    pods, _, _ = await fetch_pods(cluster, inner)
    links = _service_links(cluster, namespace, service_name, "/power")

    if not pods:
        resp = ServicePowerResponse(
            status="partial", data=None, warnings=["NO_POWER_DATA"],
        )
        return {**resp.model_dump(), "_links": links}

    total_watts = 0.0
    has_power = False
    for p in pods:
        power_data, _ = await fetch_pod_power(cluster, p.namespace, p.pod)
        if power_data and power_data.watts is not None:
            total_watts += power_data.watts
            has_power = True

    if not has_power:
        resp = ServicePowerResponse(
            status="partial", data=None, warnings=["NO_POWER_DATA"],
        )
    else:
        resp = ServicePowerResponse(
            status="success",
            data=ServicePowerData(watts=total_watts, source="kepler"),
            warnings=[],
        )
    return {**resp.model_dump(), "_links": links}
