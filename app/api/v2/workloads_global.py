"""
KCloud Monitor v2 — Workloads 전역 진입점 (11개, 포탈용).

클러스터를 먼저 고르지 않아도 Pod/서비스 기준으로 바로 진입하는 인덱스 API.
진입 규칙: canonical은 /clusters/{c}/workloads/*, 응답에 _links.canonical 포함.
데이터소스: Prometheus(kube-state-metrics, cAdvisor, Kepler).
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
    ServiceSummaryResponse,
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


@router.get("/workloads/pods", summary="전역 Pod 목록", response_model=PodListResponse)
async def list_pods_global(
    request: Request,
    params: WorkloadFilterParams = Depends(),
    cluster: Optional[str] = Query(None, description="클러스터 필터(전역 목록)"),
):
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


@router.get("/workloads/pods/summary", summary="전역 Pod 집계", response_model=PodSummaryResponse)
async def get_pods_summary_global(request: Request):
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


@router.get("/workloads/pods/{cluster}/{namespace}/{pod}", summary="전역 Pod 상세")
async def get_pod_global(request: Request, cluster: str, namespace: str, pod: str):
    data, warnings = await fetch_pod_detail(cluster, namespace, pod)
    status = "success" if data else "partial"
    resp = PodDetailResponse(status=status, data=data, warnings=warnings)
    return {**resp.model_dump(), "_links": _pod_links(cluster, namespace, pod)}


@router.get(
    "/workloads/pods/{cluster}/{namespace}/{pod}/power", summary="전역 Pod 전력",
)
async def get_pod_power_global(
    request: Request, cluster: str, namespace: str, pod: str,
):
    data, warnings = await fetch_pod_power(cluster, namespace, pod)
    status = "success" if data else "partial"
    resp = PodPowerResponse(status=status, data=data, warnings=warnings)
    return {**resp.model_dump(), "_links": _pod_links(cluster, namespace, pod, "/power")}


@router.get(
    "/workloads/pods/{cluster}/{namespace}/{pod}/containers",
    summary="전역 Pod 컨테이너",
)
async def list_pod_containers_global(
    request: Request, cluster: str, namespace: str, pod: str,
):
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
    summary="전역 Pod 가속기",
)
async def get_pod_accelerators_global(
    request: Request, cluster: str, namespace: str, pod: str,
):
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


@router.get("/workloads/services", summary="전역 서비스 목록", response_model=ServiceListResponse)
async def list_services_global(
    request: Request,
    params: WorkloadFilterParams = Depends(),
    cluster: Optional[str] = Query(None, description="클러스터 필터(전역 목록)"),
):
    names = await _resolve_clusters(cluster)
    inner = _all_params(params)
    results = await asyncio.gather(*(fetch_pods(n, inner) for n in names))

    all_pods = []
    for pods, _, _ in results:
        all_pods.extend(pods)

    groups = _group_by_service(all_pods)
    items = [_build_service_item(k, v) for k, v in groups.items()]

    if params.search:
        q = params.search.lower()
        items = [i for i in items if q in i.service_name.lower()]

    total = len(items)
    page = items[params.offset : params.offset + params.limit]
    warnings: list[str] = [] if items else ["NO_DATA"]
    return ServiceListResponse(
        status="success" if items else "partial",
        services=page, total=total, warnings=warnings,
    )


@router.get("/workloads/services/summary", summary="전역 서비스 집계", response_model=ServiceSummaryResponse)
async def get_services_summary_global(request: Request):
    names = await _resolve_clusters(None)
    inner = _all_params()
    results = await asyncio.gather(*(fetch_pods(n, inner) for n in names))

    all_pods = []
    for pods, _, _ in results:
        all_pods.extend(pods)

    groups = _group_by_service(all_pods)

    cluster_dist: dict[str, int] = {}
    for cluster_name, _, _ in groups:
        cluster_dist[cluster_name] = cluster_dist.get(cluster_name, 0) + 1

    data = ServiceSummaryData(
        total_services=len(groups), cluster_distribution=cluster_dist,
    )
    warnings: list[str] = [] if groups else ["NO_DATA"]
    return ServiceSummaryResponse(
        status="success" if groups else "partial",
        data=data, warnings=warnings,
    )


@router.get(
    "/workloads/services/{cluster}/{namespace}/{service_name}",
    summary="서비스 상세",
)
async def get_service_global(
    request: Request, cluster: str, namespace: str, service_name: str,
):
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
    summary="서비스 소속 Pod",
)
async def list_service_pods_global(
    request: Request, cluster: str, namespace: str, service_name: str,
):
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
    summary="서비스 전력 합계",
)
async def get_service_power_global(
    request: Request, cluster: str, namespace: str, service_name: str,
):
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
