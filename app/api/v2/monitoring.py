"""
KCloud Monitor v2 — Monitoring 횡단 집계 (10개: REST 8 + SSE 2).

여러 클러스터를 가로지르는 전력/메트릭 집계와 실시간 스트리밍(SSE).
v1 WebSocket은 v2에서 SSE로 통일(design_contracts §7: 15초 heartbeat, Last-Event-ID 재개).
데이터소스(구현 예정): Mimir(PromQL 횡단 질의), 전력 귀속 recording rules.
설계: sample_api.md §1.1, §8.1~§8.4, §10.2~§10.3 / 전력 계층 P8, 메트릭 M5.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.api.v2.deps import TimeseriesParams
from app.schemas.monitoring import (
    METRIC_ALLOWLIST,
    AcceleratorEfficiency,
    ClusterCounts,
    MetricSample,
    MetricsQueryResponse,
    NodeCounts,
    OverviewData,
    OverviewResponse,
    PowerBreakdownItem,
    PowerBreakdownResponse,
    PowerEfficiencyData,
    PowerEfficiencyResponse,
    PowerSummaryData,
    PowerSummaryResponse,
    PowerTimeseriesLayer,
    PowerTimeseriesResponse,
    TemperatureSeriesItem,
    TemperatureTimeseriesResponse,
    TimeseriesResponse,
)
from app.services.cluster_discovery import cluster_discovery
from app.services.power import (
    power_breakdown,
    power_efficiency,
    power_summary,
    power_timeseries,
)
from app.services.prometheus import prometheus_client

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/monitoring/overview", summary="전체 시스템 현황(KPI)", response_model=OverviewResponse)
async def get_overview(request: Request):
    """포탈 메인 KPI — 클러스터/노드/가속기/워크로드 수, 전력 합계, (추론 KPI RPS·P99·에러율 예정, A-6). [§1.1]"""
    warnings: list[str] = []

    # 1. up 쿼리로 클러스터별 인스턴스 수 집계
    up_results = await prometheus_client.instant(
        'up{cluster=~"l40s|rebellions|k8s-furiosa-rngd"}'
    )

    # 클러스터별 up/total 집계 (가속기 target 기준 — accelerator_count/healthy_count 근거)
    cluster_map: dict[str, dict] = {}
    for item in up_results:
        cluster = item.get("metric", {}).get("cluster", "unknown")
        val = float(item.get("value", [0, "0"])[1])
        if cluster not in cluster_map:
            cluster_map[cluster] = {"up": 0, "total": 0}
        cluster_map[cluster]["total"] += 1
        if val == 1.0:
            cluster_map[cluster]["up"] += 1

    healthy_count = sum(counts["up"] for counts in cluster_map.values())
    accelerator_count = sum(counts["total"] for counts in cluster_map.values())

    # 2. 클러스터 discovery 기반 관리/서비스 구분 집계 [§4.7]
    clusters_info = await cluster_discovery.get_clusters()
    cluster_counts = ClusterCounts(
        total=len(clusters_info),
        management=sum(1 for c in clusters_info.values() if c.type == "management"),
        service=sum(1 for c in clusters_info.values() if c.type == "service"),
    )

    # 노드 집계: 물리(관리 클러스터 kube_node_info) + 가상(서비스 클러스터 node_uname_info 합)
    physical_results = await prometheus_client.instant("count(kube_node_info)")
    physical_total = 0
    if physical_results:
        try:
            physical_total = int(float(physical_results[0]["value"][1]))
        except (KeyError, IndexError, ValueError):
            physical_total = 0

    physical_ready_results = await prometheus_client.instant(
        'count(kube_node_status_condition{condition="Ready",status="true"})'
    )
    physical_ready = 0
    if physical_ready_results:
        try:
            physical_ready = int(float(physical_ready_results[0]["value"][1]))
        except (KeyError, IndexError, ValueError):
            physical_ready = 0

    virtual_total = 0
    for c in clusters_info.values():
        if c.type != "service":
            continue
        vm_results = await prometheus_client.instant(
            f'count(node_uname_info{{cluster="{c.label_value}"}})'
        )
        if vm_results:
            try:
                virtual_total += int(float(vm_results[0]["value"][1]))
            except (KeyError, IndexError, ValueError):
                pass

    node_counts = NodeCounts(
        total=physical_total + virtual_total,
        physical=physical_total,
        virtual=virtual_total,
        # 서비스 클러스터 VM은 개별 헬스 관측이 아직 없어 healthy로 근사한다 (§4.7).
        healthy=physical_ready + virtual_total,
        unhealthy=physical_total - physical_ready,
    )

    # 3. L40S 평균 온도
    l40s_temp_results = await prometheus_client.instant(
        'avg(DCGM_FI_DEV_GPU_TEMP{cluster="l40s"})'
    )
    l40s_avg_temp: Optional[float] = None
    if l40s_temp_results:
        try:
            l40s_avg_temp = float(l40s_temp_results[0]["value"][1])
        except (KeyError, IndexError, ValueError):
            pass

    # 4. Furiosa 평균 온도
    furiosa_temp_results = await prometheus_client.instant(
        'avg(furiosa_npu_hw_temperature{label="peak",cluster="k8s-furiosa-rngd"})'
    )
    furiosa_avg_temp: Optional[float] = None
    if furiosa_temp_results:
        try:
            furiosa_avg_temp = float(furiosa_temp_results[0]["value"][1])
        except (KeyError, IndexError, ValueError):
            pass

    # 5. Rebellions 평균 온도 — exporter가 실측의 1/1000로 표출하므로 ×1000 보정
    #    (rbln-stat 카드 실측 39°C·18.4W와 대조 확정, 2026-08-24)
    rebellions_temp_results = await prometheus_client.instant(
        'avg({__name__="RBLN_DEVICE_STATUS:TEMPERATURE",cluster="rebellions"}) * 1000'
    )
    rebellions_avg_temp: Optional[float] = None
    if rebellions_temp_results:
        try:
            rebellions_avg_temp = float(rebellions_temp_results[0]["value"][1])
        except (KeyError, IndexError, ValueError):
            pass
    warnings.append("REBELLIONS_SCALE_CORRECTED_X1000")

    # 6. Prometheus 결과 없으면 status="partial"
    status = "partial" if not up_results else "success"

    data = OverviewData(
        clusters=cluster_counts,
        nodes=node_counts,
        accelerator_count=accelerator_count,
        healthy_count=healthy_count,
        # 키는 클러스터 라벨값으로 통일(시스템 전체 구분 키가 cluster 라벨) — 벤더명 혼용 제거
        avg_temperature={
            "l40s": l40s_avg_temp,
            "k8s-furiosa-rngd": furiosa_avg_temp,
            "rebellions": rebellions_avg_temp,  # ×1000 보정값 (REBELLIONS_SCALE_CORRECTED_X1000)
        },
    )

    return OverviewResponse(status=status, data=data, warnings=warnings)


@router.get("/monitoring/power/summary", summary="전력 요약 [P8]", response_model=PowerSummaryResponse)
async def get_power_summary(request: Request):
    """시스템 전체 전력 요약 — 계층별(서버총전력/CPU/가속기/기타) 합계. 전력 계층 P8. [§8.1]"""
    r = await power_summary()
    return PowerSummaryResponse(
        status=r["status"],
        data=PowerSummaryData(**r["data"]),
        warnings=r["warnings"],
    )


@router.get("/monitoring/power/breakdown", summary="전력 분해(다차원)", response_model=PowerBreakdownResponse)
async def get_power_breakdown(
    request: Request,
    dimension: str = Query("vendor", description="vendor|cluster|node|accelerator"),
):
    """전력 분해 — 벤더/클러스터/노드/가속기 차원별 기여도. [§8.2]"""
    r = await power_breakdown(dimension)
    return PowerBreakdownResponse(
        status=r["status"],
        dimension=dimension,
        items=[PowerBreakdownItem(**i) for i in r["data"]],
        warnings=r["warnings"],
    )


@router.get("/monitoring/power/timeseries", summary="전력 시계열(횡단)", response_model=PowerTimeseriesResponse)
async def get_power_timeseries(request: Request, params: TimeseriesParams = Depends()):
    """시스템 전력 시계열 — 계층 스택 구성용(server/cpu/accelerator). [§8.3]"""
    now = datetime.now(timezone.utc)
    start = params.start_iso(now)
    end = params.end_iso(now)
    step = params.step

    r = await power_timeseries(start, end, step)
    return PowerTimeseriesResponse(
        status=r["status"],
        layers=[PowerTimeseriesLayer(**l) for l in r["data"]],
        warnings=r["warnings"],
    )


@router.get("/monitoring/power/efficiency", summary="전력 효율", response_model=PowerEfficiencyResponse)
async def get_power_efficiency(request: Request):
    """전력 효율 — 사용률 대비 전력, PUE 추정(냉각 계수), TDP 대비 비중. [§8.4]"""
    r = await power_efficiency()
    data = r["data"]
    return PowerEfficiencyResponse(
        status=r["status"],
        data=PowerEfficiencyData(
            pue_estimate=data.get("pue_estimate"),
            accelerators=[AcceleratorEfficiency(**a) for a in data.get("accelerators", [])],
        ),
        warnings=r["warnings"],
    )


@router.get("/monitoring/metrics/query", summary="메트릭 즉시 질의 [M5]", response_model=MetricsQueryResponse)
async def query_metrics(
    request: Request,
    metric: Optional[str] = Query(None, description="조회할 메트릭 이름(카탈로그 내 허용 목록)"),
):
    """메트릭 즉시값 질의 — 허용 목록 기반 안전 질의. 메트릭 계층 M5."""
    if metric is None:
        raise HTTPException(
            status_code=400,
            detail="metric 파라미터 필수. 허용 목록: " + str(list(METRIC_ALLOWLIST.keys())),
        )
    if metric not in METRIC_ALLOWLIST:
        raise HTTPException(
            status_code=400,
            detail=f"허용되지 않은 메트릭: {metric}. 허용 목록: {list(METRIC_ALLOWLIST.keys())}",
        )

    results = await prometheus_client.instant(METRIC_ALLOWLIST[metric])

    warnings: list[str] = []
    if not results:
        warnings.append("NO_DATA")
        return MetricsQueryResponse(
            status="partial",
            metric=metric,
            results=[],
            warnings=warnings,
        )

    samples = [
        MetricSample(
            metric=item.get("metric", {}),
            value=(float(item["value"][0]), str(item["value"][1])),
        )
        for item in results
        if "value" in item
    ]

    return MetricsQueryResponse(
        status="success",
        metric=metric,
        results=samples,
        warnings=warnings,
    )


@router.get("/monitoring/metrics/timeseries", summary="메트릭 시계열(횡단)", response_model=TimeseriesResponse)
async def get_metrics_timeseries(
    request: Request,
    metric: Optional[str] = Query(None, description="조회할 메트릭 이름(카탈로그 내 허용 목록)"),
    params: TimeseriesParams = Depends(),
):
    """지정 메트릭의 횡단 시계열 — 허용 목록 기반(PromQL 인젝션 방지 계약 유지)."""
    if metric is None:
        raise HTTPException(
            status_code=400,
            detail="metric 파라미터 필수. 허용 목록: " + str(list(METRIC_ALLOWLIST.keys())),
        )
    if metric not in METRIC_ALLOWLIST:
        raise HTTPException(
            status_code=400,
            detail=f"허용되지 않은 메트릭: {metric}. 허용 목록: {list(METRIC_ALLOWLIST.keys())}",
        )

    now = datetime.now(timezone.utc)
    start = params.start_iso(now)
    end = params.end_iso(now)
    step = params.step

    results = await prometheus_client.range_query(METRIC_ALLOWLIST[metric], start, end, step)

    warnings: list[str] = []
    if not results:
        warnings.append("NO_DATA")
        return TimeseriesResponse(
            status="partial",
            metric=metric,
            series=[],
            warnings=warnings,
        )

    series = [
        MetricSample(
            metric=item.get("metric", {}),
            values=[
                (datetime.fromtimestamp(float(v[0]), tz=timezone.utc).isoformat(), str(v[1]))
                for v in item.get("values", [])
            ],
        )
        for item in results
    ]

    return TimeseriesResponse(
        status="success",
        metric=metric,
        series=series,
        warnings=warnings,
    )


@router.get(
    "/monitoring/temperature/timeseries",
    summary="온도 시계열(횡단)",
    response_model=TemperatureTimeseriesResponse,
)
async def get_temperature_timeseries(request: Request, params: TimeseriesParams = Depends()):
    """가속기/노드/IPMI 온도 통합 시계열."""
    now = datetime.now(timezone.utc)
    start = params.start_iso(now)
    end = params.end_iso(now)
    step = params.step

    warnings: list[str] = []

    # L40S GPU 온도
    l40s_results = await prometheus_client.range_query(
        'DCGM_FI_DEV_GPU_TEMP{cluster="l40s"}', start, end, step
    )
    # Furiosa NPU 온도
    furiosa_results = await prometheus_client.range_query(
        'furiosa_npu_hw_temperature{label="peak",cluster="k8s-furiosa-rngd"}', start, end, step
    )

    series: list[TemperatureSeriesItem] = []

    if not l40s_results:
        warnings.append("NO_DATA_NVIDIA")
    else:
        for item in l40s_results:
            series.append(
                TemperatureSeriesItem(
                    vendor="nvidia",
                    cluster="l40s",
                    metric_labels=item.get("metric", {}),
                    values=[
                        (datetime.fromtimestamp(float(v[0]), tz=timezone.utc).isoformat(), str(v[1]))
                        for v in item.get("values", [])
                    ],
                )
            )

    if not furiosa_results:
        warnings.append("NO_DATA_FURIOSA")
    else:
        for item in furiosa_results:
            series.append(
                TemperatureSeriesItem(
                    vendor="furiosa",
                    cluster="k8s-furiosa-rngd",
                    metric_labels=item.get("metric", {}),
                    values=[
                        (datetime.fromtimestamp(float(v[0]), tz=timezone.utc).isoformat(), str(v[1]))
                        for v in item.get("values", [])
                    ],
                )
            )

    # Rebellions NPU 온도 — exporter가 실측의 1/1000로 표출하므로 ×1000 보정
    # (rbln-stat 대조 확정, 2026-08-24)
    rebellions_results = await prometheus_client.range_query(
        '({__name__="RBLN_DEVICE_STATUS:TEMPERATURE",cluster="rebellions"}) * 1000',
        start, end, step,
    )
    if not rebellions_results:
        warnings.append("NO_DATA_REBELLIONS")
    else:
        for item in rebellions_results:
            series.append(
                TemperatureSeriesItem(
                    vendor="rebellions",
                    cluster="rebellions",
                    metric_labels=item.get("metric", {}),
                    values=[
                        (datetime.fromtimestamp(float(v[0]), tz=timezone.utc).isoformat(), str(v[1]))
                        for v in item.get("values", [])
                    ],
                )
            )
    warnings.append("REBELLIONS_SCALE_CORRECTED_X1000")

    status = "success" if series else "partial"

    return TemperatureTimeseriesResponse(
        status=status,
        series=series,
        warnings=warnings,
    )


@router.get("/monitoring/stream/power", summary="실시간 전력 스트림(SSE)")
async def stream_power(request: Request):
    """전력 실시간 SSE — REST와 동일 응답 모델의 data 이벤트 + 15초 heartbeat. [§10.2]"""
    _now_iso = lambda: datetime.now(timezone.utc).isoformat()
    event_id = 0

    async def generate():
        nonlocal event_id
        while True:
            if await request.is_disconnected():
                break

            # heartbeat
            event_id += 1
            yield f"id: {event_id}\nevent: heartbeat\ndata: {json.dumps({'observed_at': _now_iso()})}\n\n"

            # 데이터 폴링
            try:
                r = await power_summary()
                payload = {
                    "status": r["status"],
                    "data": r["data"],
                    "warnings": r["warnings"],
                    "observed_at": _now_iso(),
                }
                event_id += 1
                yield f"id: {event_id}\nevent: power\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            except Exception:
                logger.exception("SSE power poll error")
                event_id += 1
                yield f"id: {event_id}\nevent: error\ndata: {json.dumps({'error': 'poll_failed', 'observed_at': _now_iso()})}\n\n"

            await asyncio.sleep(15)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/monitoring/stream/metrics", summary="실시간 메트릭 스트림(SSE)")
async def stream_metrics(
    request: Request,
    metric: Optional[str] = Query(None, description="조회할 메트릭 이름(허용 목록)"),
):
    """메트릭 실시간 SSE — 15초 heartbeat, allowlist 기반 주기 폴링. [§10.3]"""
    if metric is None or metric not in METRIC_ALLOWLIST:
        available = list(METRIC_ALLOWLIST.keys())
        raise HTTPException(
            status_code=400,
            detail=f"metric 파라미터 필수(허용 목록). 사용 가능: {available}",
        )

    _now_iso = lambda: datetime.now(timezone.utc).isoformat()
    event_id = 0

    async def generate():
        nonlocal event_id
        while True:
            if await request.is_disconnected():
                break

            # heartbeat
            event_id += 1
            yield f"id: {event_id}\nevent: heartbeat\ndata: {json.dumps({'observed_at': _now_iso()})}\n\n"

            # 데이터 폴링
            try:
                results = await prometheus_client.instant(METRIC_ALLOWLIST[metric])
                samples = [
                    {
                        "metric": item.get("metric", {}),
                        "value": [item["value"][0], item["value"][1]],
                    }
                    for item in results
                    if "value" in item
                ]
                event_id += 1
                payload = {
                    "status": "success" if samples else "partial",
                    "metric": metric,
                    "results": samples,
                    "observed_at": _now_iso(),
                }
                yield f"id: {event_id}\nevent: metric\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            except Exception:
                logger.exception("SSE metric poll error for %s", metric)
                event_id += 1
                yield f"id: {event_id}\nevent: error\ndata: {json.dumps({'error': 'poll_failed', 'observed_at': _now_iso()})}\n\n"

            await asyncio.sleep(15)

    return StreamingResponse(generate(), media_type="text/event-stream")
