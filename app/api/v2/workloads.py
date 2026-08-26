"""
KCloud Monitor v2 — Workloads, 클러스터 범위 canonical (11개) + 별칭 2개.

Pod/Container/Namespace 워크로드 모니터링. canonical 경로는 /clusters/{c}/workloads/*
(전역 진입점 /workloads/* 는 workloads_global.py).
데이터소스: Prometheus(kube-state-metrics, cAdvisor, Kepler).
"""
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request

from app.api.v2.deps import WorkloadFilterParams
from app.schemas.workloads import (
    ContainerDetailResponse,
    ContainerItem,
    ContainerListResponse,
    ContainerMetricsData,
    ContainerMetricsResponse,
    NamespaceItem,
    NamespaceListResponse,
    NamespaceSummaryData,
    NamespaceSummaryResponse,
    PodAcceleratorItem,
    PodAcceleratorResponse,
    PodDetailData,
    PodDetailResponse,
    PodItem,
    PodListResponse,
    PodPowerData,
    PodPowerResponse,
    PodSummaryData,
    PodSummaryResponse,
)
from app.services.cluster_discovery import cluster_discovery, cluster_label
from app.services.prometheus import prometheus_client

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _cl(cluster: str) -> str:
    return _esc(cluster_label(cluster))


def _first_value(results: list[dict]) -> Optional[float]:
    if not results:
        return None
    try:
        return float(results[0]["value"][1])
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def _ts_to_iso(val: Optional[float]) -> Optional[str]:
    if val is None:
        return None
    return datetime.fromtimestamp(val, tz=timezone.utc).isoformat()


def _derive_service_name(
    labels: dict[str, str],
    created_by_name: Optional[str],
    pod_name: str,
) -> Optional[str]:
    svc = labels.get("label_app_kubernetes_io_name")
    if svc:
        return svc
    svc = labels.get("label_k8s_app")
    if svc:
        return svc
    if created_by_name:
        return re.sub(r"-[a-f0-9]{7,10}$", "", created_by_name)
    stripped = re.sub(r"(-[a-z0-9]{5,10}){1,2}$", "", pod_name)
    return stripped if stripped != pod_name else None


def _scalar_map(results: list[dict]) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for item in results:
        m = item.get("metric", {})
        key = (m.get("namespace", ""), m.get("pod", ""))
        val = _first_value([item])
        if val is not None:
            out[key] = val
    return out


# ---------------------------------------------------------------------------
# Internal fetch functions (also used by workloads_global.py)
# ---------------------------------------------------------------------------


async def fetch_pods(
    cluster: str, params: WorkloadFilterParams,
) -> tuple[list[PodItem], int, list[str]]:
    c = _cl(cluster)

    info_results = await prometheus_client.instant(f'kube_pod_info{{cluster="{c}"}}')
    phase_results = await prometheus_client.instant(
        f'kube_pod_status_phase{{cluster="{c}"}} == 1'
    )
    created_results = await prometheus_client.instant(f'kube_pod_created{{cluster="{c}"}}')
    label_results = await prometheus_client.instant(f'kube_pod_labels{{cluster="{c}"}}')
    restart_results = await prometheus_client.instant(
        f'sum by (namespace, pod) (kube_pod_container_status_restarts_total{{cluster="{c}"}})'
    )
    cnt_results = await prometheus_client.instant(
        f'count by (namespace, pod) (kube_pod_container_info{{cluster="{c}"}})'
    )
    cpu_results = await prometheus_client.instant(
        f'sum by (namespace, pod) (rate(container_cpu_usage_seconds_total{{cluster="{c}",container!=""}}[5m]))'
    )
    mem_results = await prometheus_client.instant(
        f'sum by (namespace, pod) (container_memory_working_set_bytes{{cluster="{c}",container!=""}})'
    )

    pod_map: dict[tuple[str, str], dict] = {}
    for item in info_results:
        m = item.get("metric", {})
        key = (m.get("namespace", ""), m.get("pod", ""))
        pod_map[key] = {
            "node": m.get("node"),
            "host_ip": m.get("host_ip"),
            "pod_ip": m.get("pod_ip"),
            "created_by_kind": m.get("created_by_kind"),
            "created_by_name": m.get("created_by_name"),
        }

    phase_map: dict[tuple[str, str], str] = {}
    for item in phase_results:
        m = item.get("metric", {})
        phase_map[(m.get("namespace", ""), m.get("pod", ""))] = m.get("phase", "Unknown")

    created_map: dict[tuple[str, str], Optional[str]] = {}
    for item in created_results:
        m = item.get("metric", {})
        key = (m.get("namespace", ""), m.get("pod", ""))
        created_map[key] = _ts_to_iso(_first_value([item]))

    labels_map: dict[tuple[str, str], dict[str, str]] = {}
    for item in label_results:
        m = item.get("metric", {})
        labels_map[(m.get("namespace", ""), m.get("pod", ""))] = m

    restarts = _scalar_map(restart_results)
    counts = _scalar_map(cnt_results)
    cpus = _scalar_map(cpu_results)
    mems = _scalar_map(mem_results)

    items: list[PodItem] = []
    for key, info in pod_map.items():
        ns, pod_name = key
        labels = labels_map.get(key, {})
        svc = _derive_service_name(labels, info.get("created_by_name"), pod_name)
        cnt_val = counts.get(key)
        rst_val = restarts.get(key)
        items.append(
            PodItem(
                namespace=ns,
                pod=pod_name,
                cluster=cluster,
                node=info.get("node"),
                phase=phase_map.get(key),
                pod_ip=info.get("pod_ip"),
                host_ip=info.get("host_ip"),
                created_at=created_map.get(key),
                workload_type=info.get("created_by_kind"),
                workload_name=info.get("created_by_name"),
                service_name=svc,
                container_count=int(cnt_val) if cnt_val is not None else None,
                restart_count=int(rst_val) if rst_val is not None else None,
                cpu_usage=cpus.get(key),
                memory_usage_bytes=mems.get(key),
            )
        )

    if params.namespace:
        items = [i for i in items if i.namespace == params.namespace]
    if params.status:
        sl = params.status.lower()
        items = [i for i in items if i.phase and i.phase.lower() == sl]
    if params.workload_type:
        wl = params.workload_type.lower()
        items = [i for i in items if i.workload_type and i.workload_type.lower() == wl]
    if params.service_name:
        sn = params.service_name.lower()
        items = [i for i in items if i.service_name and sn in i.service_name.lower()]
    if params.search:
        q = params.search.lower()
        items = [i for i in items if q in i.pod.lower() or q in i.namespace.lower()]

    if params.sort_by:
        reverse = params.sort_order == "desc"
        items.sort(key=lambda x: getattr(x, params.sort_by, "") or "", reverse=reverse)

    total = len(items)
    items = items[params.offset : params.offset + params.limit]
    warnings: list[str] = [] if pod_map else ["NO_DATA"]
    return items, total, warnings


async def fetch_pods_summary(cluster: str) -> tuple[PodSummaryData, list[str]]:
    c = _cl(cluster)

    phase_results = await prometheus_client.instant(
        f'count by (phase) (kube_pod_status_phase{{cluster="{c}"}} == 1)'
    )
    ns_results = await prometheus_client.instant(
        f'count by (namespace) (kube_pod_info{{cluster="{c}"}})'
    )

    phase_counts: dict[str, int] = {}
    for item in phase_results:
        phase = item.get("metric", {}).get("phase", "Unknown")
        val = _first_value([item])
        if val is not None:
            phase_counts[phase] = int(val)

    ns_dist: dict[str, int] = {}
    for item in ns_results:
        ns = item.get("metric", {}).get("namespace", "")
        val = _first_value([item])
        if val is not None:
            ns_dist[ns] = int(val)

    total = sum(phase_counts.values())
    data = PodSummaryData(
        total_count=total,
        running_count=phase_counts.get("Running", 0),
        pending_count=phase_counts.get("Pending", 0),
        succeeded_count=phase_counts.get("Succeeded", 0),
        failed_count=phase_counts.get("Failed", 0),
        unknown_count=phase_counts.get("Unknown", 0),
        namespace_distribution=ns_dist,
    )
    warnings: list[str] = [] if total > 0 else ["NO_DATA"]
    return data, warnings


async def fetch_pod_detail(
    cluster: str, namespace: str, pod: str,
) -> tuple[Optional[PodDetailData], list[str]]:
    c = _cl(cluster)
    label = f'cluster="{c}",namespace="{_esc(namespace)}",pod="{_esc(pod)}"'

    info_results = await prometheus_client.instant(f"kube_pod_info{{{label}}}")
    if not info_results:
        return None, ["NO_DATA"]

    m = info_results[0].get("metric", {})

    phase_results = await prometheus_client.instant(
        f"kube_pod_status_phase{{{label}}} == 1"
    )
    created_results = await prometheus_client.instant(f"kube_pod_created{{{label}}}")
    label_results = await prometheus_client.instant(f"kube_pod_labels{{{label}}}")
    cpu_results = await prometheus_client.instant(
        f'sum(rate(container_cpu_usage_seconds_total{{{label},container!=""}}[5m]))'
    )
    mem_results = await prometheus_client.instant(
        f'sum(container_memory_working_set_bytes{{{label},container!=""}})'
    )
    cpu_req = await prometheus_client.instant(
        f'sum(kube_pod_container_resource_requests{{{label},resource="cpu"}})'
    )
    cpu_lim = await prometheus_client.instant(
        f'sum(kube_pod_container_resource_limits{{{label},resource="cpu"}})'
    )
    mem_req = await prometheus_client.instant(
        f'sum(kube_pod_container_resource_requests{{{label},resource="memory"}})'
    )
    mem_lim = await prometheus_client.instant(
        f'sum(kube_pod_container_resource_limits{{{label},resource="memory"}})'
    )
    restart_results = await prometheus_client.instant(
        f"sum(kube_pod_container_status_restarts_total{{{label}}})"
    )
    cnt_results = await prometheus_client.instant(
        f"count(kube_pod_container_info{{{label}}})"
    )

    phase = None
    if phase_results:
        phase = phase_results[0].get("metric", {}).get("phase")

    labels = label_results[0].get("metric", {}) if label_results else {}
    svc = _derive_service_name(labels, m.get("created_by_name"), pod)

    cnt_val = _first_value(cnt_results)
    rst_val = _first_value(restart_results)

    data = PodDetailData(
        namespace=namespace,
        pod=pod,
        cluster=cluster,
        uid=m.get("uid"),
        node=m.get("node"),
        phase=phase,
        pod_ip=m.get("pod_ip"),
        host_ip=m.get("host_ip"),
        created_at=_ts_to_iso(_first_value(created_results)),
        workload_type=m.get("created_by_kind"),
        workload_name=m.get("created_by_name"),
        service_name=svc,
        container_count=int(cnt_val) if cnt_val is not None else None,
        restart_count=int(rst_val) if rst_val is not None else None,
        cpu_usage=_first_value(cpu_results),
        memory_usage_bytes=_first_value(mem_results),
        cpu_requests=_first_value(cpu_req),
        cpu_limits=_first_value(cpu_lim),
        memory_requests_bytes=_first_value(mem_req),
        memory_limits_bytes=_first_value(mem_lim),
    )
    return data, []


async def fetch_pod_containers(
    cluster: str, namespace: str, pod: str,
) -> tuple[list[ContainerItem], list[str]]:
    c = _cl(cluster)
    label = f'cluster="{c}",namespace="{_esc(namespace)}",pod="{_esc(pod)}"'

    info_results = await prometheus_client.instant(f"kube_pod_container_info{{{label}}}")
    if not info_results:
        return [], ["NO_DATA"]

    running_results = await prometheus_client.instant(
        f"kube_pod_container_status_running{{{label}}}"
    )
    waiting_results = await prometheus_client.instant(
        f"kube_pod_container_status_waiting{{{label}}}"
    )
    terminated_results = await prometheus_client.instant(
        f"kube_pod_container_status_terminated{{{label}}}"
    )
    restart_results = await prometheus_client.instant(
        f"kube_pod_container_status_restarts_total{{{label}}}"
    )
    cpu_results = await prometheus_client.instant(
        f'rate(container_cpu_usage_seconds_total{{{label},container!=""}}[5m])'
    )
    mem_results = await prometheus_client.instant(
        f'container_memory_working_set_bytes{{{label},container!=""}}'
    )
    req_results = await prometheus_client.instant(
        f"kube_pod_container_resource_requests{{{label}}}"
    )
    lim_results = await prometheus_client.instant(
        f"kube_pod_container_resource_limits{{{label}}}"
    )

    def _by_c(results: list[dict]) -> dict[str, Optional[float]]:
        out: dict[str, Optional[float]] = {}
        for item in results:
            cn = item.get("metric", {}).get("container", "")
            if cn:
                out[cn] = _first_value([item])
        return out

    running_m = _by_c(running_results)
    waiting_m = _by_c(waiting_results)
    terminated_m = _by_c(terminated_results)
    restart_m = _by_c(restart_results)
    cpu_m = _by_c(cpu_results)
    mem_m = _by_c(mem_results)

    req_map: dict[tuple[str, str], float] = {}
    for item in req_results:
        mi = item.get("metric", {})
        cn = mi.get("container", "")
        res = mi.get("resource", "")
        val = _first_value([item])
        if cn and res and val is not None:
            req_map[(cn, res)] = val

    lim_map: dict[tuple[str, str], float] = {}
    for item in lim_results:
        mi = item.get("metric", {})
        cn = mi.get("container", "")
        res = mi.get("resource", "")
        val = _first_value([item])
        if cn and res and val is not None:
            lim_map[(cn, res)] = val

    def _status(cn: str) -> Optional[str]:
        if running_m.get(cn) == 1.0:
            return "running"
        if waiting_m.get(cn) == 1.0:
            return "waiting"
        if terminated_m.get(cn) == 1.0:
            return "terminated"
        return None

    containers: list[ContainerItem] = []
    for item in info_results:
        mi = item.get("metric", {})
        cn = mi.get("container", "")
        if not cn:
            continue
        rst = restart_m.get(cn)
        containers.append(
            ContainerItem(
                namespace=namespace,
                pod=pod,
                container=cn,
                cluster=cluster,
                container_id=mi.get("container_id"),
                image=mi.get("image"),
                status=_status(cn),
                restart_count=int(rst) if rst is not None else None,
                cpu_usage=cpu_m.get(cn),
                memory_usage_bytes=mem_m.get(cn),
                cpu_requests=req_map.get((cn, "cpu")),
                cpu_limits=lim_map.get((cn, "cpu")),
                memory_requests_bytes=req_map.get((cn, "memory")),
                memory_limits_bytes=lim_map.get((cn, "memory")),
            )
        )
    return containers, []


async def fetch_container_metrics(
    cluster: str, namespace: str, pod: str, container_name: str,
) -> tuple[ContainerMetricsData, list[str]]:
    c = _cl(cluster)
    label = (
        f'cluster="{c}",namespace="{_esc(namespace)}",'
        f'pod="{_esc(pod)}",container="{_esc(container_name)}"'
    )
    cpu_results = await prometheus_client.instant(
        f"rate(container_cpu_usage_seconds_total{{{label}}}[5m])"
    )
    mem_results = await prometheus_client.instant(
        f"container_memory_working_set_bytes{{{label}}}"
    )
    has_data = bool(cpu_results or mem_results)
    data = ContainerMetricsData(
        cpu_usage=_first_value(cpu_results),
        memory_usage_bytes=_first_value(mem_results),
    )
    return data, [] if has_data else ["NO_DATA"]


async def fetch_pod_power(
    cluster: str, namespace: str, pod: str,
) -> tuple[Optional[PodPowerData], list[str]]:
    c = _cl(cluster)
    label = f'cluster="{c}",namespace="{_esc(namespace)}",pod="{_esc(pod)}"'

    results = await prometheus_client.instant(
        f"sum(rate(kepler_container_joules_total{{{label}}}[5m]))"
    )
    if not results:
        label_alt = (
            f'cluster="{c}",pod_namespace="{_esc(namespace)}",'
            f'pod_name="{_esc(pod)}"'
        )
        results = await prometheus_client.instant(
            f"sum(rate(kepler_container_joules_total{{{label_alt}}}[5m]))"
        )

    if not results:
        return None, ["NO_POWER_DATA"]

    return PodPowerData(watts=_first_value(results), source="kepler"), []


async def fetch_pod_accelerators(
    cluster: str, namespace: str, pod: str,
) -> tuple[list[PodAcceleratorItem], list[str]]:
    info = await cluster_discovery.get_cluster(cluster)
    if not info or info.vendor != "nvidia":
        return [], ["POD_ACCELERATOR_DATA_NOT_AVAILABLE"]

    c = _cl(cluster)
    results = await prometheus_client.instant(
        f'DCGM_FI_DEV_GPU_UTIL{{cluster="{c}",exported_pod="{_esc(pod)}"}}'
    )
    if not results:
        return [], ["POD_ACCELERATOR_DATA_NOT_AVAILABLE"]

    items: list[PodAcceleratorItem] = []
    for item in results:
        mi = item.get("metric", {})
        items.append(
            PodAcceleratorItem(
                acc_id=mi.get("gpu") or mi.get("UUID"),
                vendor="nvidia",
                model_name=mi.get("modelName"),
            )
        )
    return items, []


async def fetch_cluster_containers(
    cluster: str, params: WorkloadFilterParams,
) -> tuple[list[ContainerItem], int, list[str]]:
    c = _cl(cluster)

    info_results = await prometheus_client.instant(
        f'kube_pod_container_info{{cluster="{c}"}}'
    )
    if not info_results:
        return [], 0, ["NO_DATA"]

    running_results = await prometheus_client.instant(
        f'kube_pod_container_status_running{{cluster="{c}"}}'
    )
    restart_results = await prometheus_client.instant(
        f'kube_pod_container_status_restarts_total{{cluster="{c}"}}'
    )
    cpu_results = await prometheus_client.instant(
        f'rate(container_cpu_usage_seconds_total{{cluster="{c}",container!=""}}[5m])'
    )
    mem_results = await prometheus_client.instant(
        f'container_memory_working_set_bytes{{cluster="{c}",container!=""}}'
    )

    def _by_key(results: list[dict]) -> dict[tuple[str, str, str], float]:
        out: dict[tuple[str, str, str], float] = {}
        for item in results:
            mi = item.get("metric", {})
            key = (mi.get("namespace", ""), mi.get("pod", ""), mi.get("container", ""))
            val = _first_value([item])
            if val is not None:
                out[key] = val
        return out

    running_m = _by_key(running_results)
    restart_m = _by_key(restart_results)
    cpu_m = _by_key(cpu_results)
    mem_m = _by_key(mem_results)

    items: list[ContainerItem] = []
    for item in info_results:
        mi = item.get("metric", {})
        ns = mi.get("namespace", "")
        pd = mi.get("pod", "")
        cn = mi.get("container", "")
        if not cn:
            continue
        key = (ns, pd, cn)
        rst = restart_m.get(key)
        items.append(
            ContainerItem(
                namespace=ns,
                pod=pd,
                container=cn,
                cluster=cluster,
                container_id=mi.get("container_id"),
                image=mi.get("image"),
                status="running" if running_m.get(key) == 1.0 else None,
                restart_count=int(rst) if rst is not None else None,
                cpu_usage=cpu_m.get(key),
                memory_usage_bytes=mem_m.get(key),
            )
        )

    if params.namespace:
        items = [i for i in items if i.namespace == params.namespace]
    if params.search:
        q = params.search.lower()
        items = [i for i in items if q in i.container.lower() or q in i.pod.lower()]

    total = len(items)
    items = items[params.offset : params.offset + params.limit]
    return items, total, []


async def fetch_container_by_id(
    cluster: str, container_id: str,
) -> tuple[Optional[ContainerItem], list[str]]:
    c = _cl(cluster)
    info_results = await prometheus_client.instant(
        f'kube_pod_container_info{{cluster="{c}"}}'
    )

    query_id = container_id.split("://", 1)[-1] if "://" in container_id else container_id

    for item in info_results:
        mi = item.get("metric", {})
        raw_id = mi.get("container_id", "")
        stored_id = raw_id.split("://", 1)[-1] if "://" in raw_id else raw_id
        if stored_id and stored_id.startswith(query_id):
            ns = mi.get("namespace", "")
            pd = mi.get("pod", "")
            cn = mi.get("container", "")
            containers, _ = await fetch_pod_containers(cluster, ns, pd)
            for ci in containers:
                if ci.container == cn:
                    return ci, []
            return (
                ContainerItem(
                    namespace=ns, pod=pd, container=cn, cluster=cluster,
                    container_id=raw_id, image=mi.get("image"),
                ),
                [],
            )

    return None, ["NO_DATA"]


async def fetch_namespaces(
    cluster: str, params: WorkloadFilterParams,
) -> tuple[list[NamespaceItem], int, list[str]]:
    c = _cl(cluster)

    created_results = await prometheus_client.instant(
        f'kube_namespace_created{{cluster="{c}"}}'
    )
    pod_cnt_results = await prometheus_client.instant(
        f'count by (namespace) (kube_pod_info{{cluster="{c}"}})'
    )

    if not created_results:
        created_results = await prometheus_client.instant(
            f'kube_namespace_labels{{cluster="{c}"}}'
        )

    created_map: dict[str, Optional[str]] = {}
    ns_names: set[str] = set()
    for item in created_results:
        ns = item.get("metric", {}).get("namespace", "")
        if ns:
            ns_names.add(ns)
            val = _first_value([item])
            created_map[ns] = _ts_to_iso(val) if val and val > 0 else None

    pod_cnt_map: dict[str, int] = {}
    for item in pod_cnt_results:
        ns = item.get("metric", {}).get("namespace", "")
        val = _first_value([item])
        if ns and val is not None:
            pod_cnt_map[ns] = int(val)
            ns_names.add(ns)

    sorted_ns = sorted(ns_names)
    if params.search:
        sorted_ns = [ns for ns in sorted_ns if params.search.lower() in ns.lower()]

    total = len(sorted_ns)
    page = sorted_ns[params.offset : params.offset + params.limit]

    items = [
        NamespaceItem(
            namespace=ns,
            cluster=cluster,
            pod_count=pod_cnt_map.get(ns),
            created_at=created_map.get(ns),
        )
        for ns in page
    ]
    warnings: list[str] = [] if ns_names else ["NO_DATA"]
    return items, total, warnings


async def fetch_namespace_summary(
    cluster: str, namespace: str,
) -> tuple[NamespaceSummaryData, list[str]]:
    c = _cl(cluster)
    label = f'cluster="{c}",namespace="{_esc(namespace)}"'

    pod_cnt = await prometheus_client.instant(f"count(kube_pod_info{{{label}}})")
    container_cnt = await prometheus_client.instant(
        f"count(kube_pod_container_info{{{label}}})"
    )
    cpu_results = await prometheus_client.instant(
        f'sum(rate(container_cpu_usage_seconds_total{{{label},container!=""}}[5m]))'
    )
    mem_results = await prometheus_client.instant(
        f'sum(container_memory_working_set_bytes{{{label},container!=""}})'
    )
    cpu_req = await prometheus_client.instant(
        f'sum(kube_pod_container_resource_requests{{{label},resource="cpu"}})'
    )
    mem_req = await prometheus_client.instant(
        f'sum(kube_pod_container_resource_requests{{{label},resource="memory"}})'
    )

    pc = _first_value(pod_cnt)
    cc = _first_value(container_cnt)

    data = NamespaceSummaryData(
        namespace=namespace,
        cluster=cluster,
        pod_count=int(pc) if pc is not None else 0,
        container_count=int(cc) if cc is not None else 0,
        cpu_usage=_first_value(cpu_results),
        memory_usage_bytes=_first_value(mem_results),
        cpu_requests=_first_value(cpu_req),
        memory_requests_bytes=_first_value(mem_req),
    )
    warnings: list[str] = [] if pc else ["NO_DATA"]
    return data, warnings


# ---------------------------------------------------------------------------
# Route handlers — Pods
# ---------------------------------------------------------------------------


@router.get("/clusters/{cluster}/workloads/pods", summary="Pod 목록", response_model=PodListResponse)
async def list_pods(
    request: Request, cluster: str, params: WorkloadFilterParams = Depends(),
):
    pods, total, warnings = await fetch_pods(cluster, params)
    status = "success" if not warnings else "partial"
    return PodListResponse(status=status, pods=pods, total=total, warnings=warnings)


@router.get(
    "/clusters/{cluster}/workloads/pods/summary",
    summary="Pod 집계 요약",
    response_model=PodSummaryResponse,
)
async def get_pods_summary(request: Request, cluster: str):
    data, warnings = await fetch_pods_summary(cluster)
    status = "success" if not warnings else "partial"
    return PodSummaryResponse(status=status, data=data, warnings=warnings)


@router.get(
    "/clusters/{cluster}/workloads/pods/{namespace}/{pod}",
    summary="Pod 상세",
    response_model=PodDetailResponse,
)
async def get_pod(request: Request, cluster: str, namespace: str, pod: str):
    data, warnings = await fetch_pod_detail(cluster, namespace, pod)
    status = "success" if data else "partial"
    return PodDetailResponse(status=status, data=data, warnings=warnings)


@router.get(
    "/clusters/{cluster}/workloads/pods/{namespace}/{pod}/power",
    summary="Pod 전력 귀속 [P7]",
    response_model=PodPowerResponse,
)
async def get_pod_power(request: Request, cluster: str, namespace: str, pod: str):
    data, warnings = await fetch_pod_power(cluster, namespace, pod)
    status = "success" if data else "partial"
    return PodPowerResponse(status=status, data=data, warnings=warnings)


@router.get(
    "/clusters/{cluster}/workloads/pods/{namespace}/{pod}/containers",
    summary="Pod 컨테이너 목록",
    response_model=ContainerListResponse,
)
async def list_pod_containers(
    request: Request, cluster: str, namespace: str, pod: str,
):
    containers, warnings = await fetch_pod_containers(cluster, namespace, pod)
    status = "success" if not warnings else "partial"
    return ContainerListResponse(
        status=status, containers=containers, total=len(containers), warnings=warnings,
    )


@router.get(
    "/clusters/{cluster}/workloads/pods/{namespace}/{pod}/containers/{container_name}/metrics",
    summary="컨테이너 메트릭",
    response_model=ContainerMetricsResponse,
)
async def get_container_metrics(
    request: Request, cluster: str, namespace: str, pod: str, container_name: str,
):
    data, warnings = await fetch_container_metrics(cluster, namespace, pod, container_name)
    status = "success" if not warnings else "partial"
    return ContainerMetricsResponse(status=status, data=data, warnings=warnings)


@router.get(
    "/clusters/{cluster}/workloads/pods/{namespace}/{pod}/accelerators",
    summary="Pod 가속기 할당",
    response_model=PodAcceleratorResponse,
)
async def get_pod_accelerators(
    request: Request, cluster: str, namespace: str, pod: str,
):
    items, warnings = await fetch_pod_accelerators(cluster, namespace, pod)
    status = "success" if not warnings else "partial"
    return PodAcceleratorResponse(status=status, data=items, warnings=warnings)


# ---------------------------------------------------------------------------
# Route handlers — Containers (클러스터 전역)
# ---------------------------------------------------------------------------


@router.get(
    "/clusters/{cluster}/workloads/containers",
    summary="컨테이너 목록",
    response_model=ContainerListResponse,
)
async def list_containers(
    request: Request, cluster: str, params: WorkloadFilterParams = Depends(),
):
    containers, total, warnings = await fetch_cluster_containers(cluster, params)
    status = "success" if not warnings else "partial"
    return ContainerListResponse(
        status=status, containers=containers, total=total, warnings=warnings,
    )


@router.get(
    "/clusters/{cluster}/workloads/containers/{container_id}",
    summary="컨테이너 상세",
    response_model=ContainerDetailResponse,
)
async def get_container(request: Request, cluster: str, container_id: str):
    data, warnings = await fetch_container_by_id(cluster, container_id)
    status = "success" if data else "partial"
    return ContainerDetailResponse(status=status, data=data, warnings=warnings)


# ---------------------------------------------------------------------------
# Route handlers — Namespaces
# ---------------------------------------------------------------------------


@router.get(
    "/clusters/{cluster}/namespaces",
    summary="네임스페이스 목록",
    response_model=NamespaceListResponse,
)
async def list_namespaces(
    request: Request, cluster: str, params: WorkloadFilterParams = Depends(),
):
    items, total, warnings = await fetch_namespaces(cluster, params)
    status = "success" if not warnings else "partial"
    return NamespaceListResponse(
        status=status, namespaces=items, total=total, warnings=warnings,
    )


@router.get(
    "/clusters/{cluster}/namespaces/{namespace}/summary",
    summary="네임스페이스 요약",
    response_model=NamespaceSummaryResponse,
)
async def get_namespace_summary(request: Request, cluster: str, namespace: str):
    data, warnings = await fetch_namespace_summary(cluster, namespace)
    status = "success" if not warnings else "partial"
    return NamespaceSummaryResponse(status=status, data=data, warnings=warnings)


# ---------------------------------------------------------------------------
# 별칭(단축 경로)
# ---------------------------------------------------------------------------


@router.get(
    "/clusters/{cluster}/pods/{namespace}/{pod}",
    summary="Pod 상세(단축 경로)",
)
async def get_pod_alias(request: Request, cluster: str, namespace: str, pod: str):
    data, warnings = await fetch_pod_detail(cluster, namespace, pod)
    status = "success" if data else "partial"
    resp = PodDetailResponse(status=status, data=data, warnings=warnings)
    return {
        **resp.model_dump(),
        "_links": {
            "self": f"/api/v2/clusters/{cluster}/pods/{namespace}/{pod}",
            "canonical": f"/api/v2/clusters/{cluster}/workloads/pods/{namespace}/{pod}",
        },
    }


@router.get(
    "/clusters/{cluster}/containers/{container_id}",
    summary="컨테이너 상세(단축 경로)",
)
async def get_container_alias(request: Request, cluster: str, container_id: str):
    data, warnings = await fetch_container_by_id(cluster, container_id)
    status = "success" if data else "partial"
    resp = ContainerDetailResponse(status=status, data=data, warnings=warnings)
    return {
        **resp.model_dump(),
        "_links": {
            "self": f"/api/v2/clusters/{cluster}/containers/{container_id}",
            "canonical": f"/api/v2/clusters/{cluster}/workloads/containers/{container_id}",
        },
    }
