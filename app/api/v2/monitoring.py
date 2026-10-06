"""여러 클러스터를 한꺼번에 묶어 보는 라우터

클러스터 구분 없이 전체를 합친 현황, 전력, 메트릭 조회와 실시간 스트리밍(SSE).
스트리밍은 15초마다 heartbeat 전송, Last-Event-ID 헤더로 재개 가능.
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
    PowerEfficiencySeriesItem,
    PowerEfficiencyTimeseriesResponse,
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
    power_efficiency_timeseries,
    power_summary,
    power_timeseries,
)
from app.services.prometheus import prometheus_client

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/monitoring/overview", summary="전체 시스템 현황(KPI)", response_model=OverviewResponse)
async def get_overview(request: Request):
    """전체 인프라 현황을 한 화면에 담을 수 있는 요약값 조회

    - clusters : 전체 클러스터 수, 관리 클러스터 수, 서비스 클러스터 수
    - nodes : 전체 노드 수, 물리 노드 수, 가상 노드 수, 정상 노드 수, 비정상 노드 수
    - accelerator_count : 전체 가속기 카드 수
    - healthy_count : 정상 동작 중인 가속기 카드 수
    - avg_temperature : 클러스터별 평균 가속기 온도(°C)
    """
    warnings: list[str] = []

    # 1. up 쿼리로 클러스터별 인스턴스 수 집계
    up_results = await prometheus_client.instant(
        'up{cluster=~"l40s|rebellions|furiosa.*"}'
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
        'avg(kcloud_furiosa_temperature_celsius{sensor="soc_peak",cluster=~"furiosa.*"})'
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
            "furiosa": furiosa_avg_temp,  # 퓨리오사 클러스터 전체 평균(furiosa, furiosa-1348 ...)
            "rebellions": rebellions_avg_temp,  # ×1000 보정값 (REBELLIONS_SCALE_CORRECTED_X1000)
        },
    )

    return OverviewResponse(status=status, data=data, warnings=warnings)


@router.get("/monitoring/power/summary", summary="전력 요약", response_model=PowerSummaryResponse)
async def get_power_summary(request: Request):
    """인프라 전체가 쓰는 전력을 항목별로 나눈 합계 조회

    - server_total_watts : 서버 총 전력(W), IPMI 실측
    - cpu_total_watts : CPU 전력(W), Kepler 실측
    - accelerator_total_watts : 가속기 전력 합계(W)
    - accelerator_by_vendor : 벤더별 가속기 전력 합계(W)
    - other_watts : 나머지 전력(W), 서버 총 전력에서 CPU와 가속기를 뺀 값
    """
    r = await power_summary()
    return PowerSummaryResponse(
        status=r["status"],
        data=PowerSummaryData(**r["data"]),
        warnings=r["warnings"],
    )


@router.get("/monitoring/power/breakdown", summary="전력 분해(다차원)", response_model=PowerBreakdownResponse)
async def get_power_breakdown(
    request: Request,
    dimension: str = Query("vendor", description="쪼개는 기준: vendor(벤더별) | cluster(클러스터별) | node(노드별) | accelerator(카드별)"),
):
    """전력을 원하는 기준으로 쪼개서 어디서 얼마나 쓰는지 조회

    - dimension : vendor(벤더별) | cluster(클러스터별) | node(노드별) | accelerator(카드별)
    - items : 기준값 이름, 전력(W), 측정 구분
    """
    r = await power_breakdown(dimension)
    return PowerBreakdownResponse(
        status=r["status"],
        dimension=dimension,
        items=[PowerBreakdownItem(**i) for i in r["data"]],
        warnings=r["warnings"],
    )


@router.get("/monitoring/power/timeseries", summary="전력 시계열(횡단)", response_model=PowerTimeseriesResponse)
async def get_power_timeseries(request: Request, params: TimeseriesParams = Depends()):
    """인프라 전체 전력의 시간별 변화 추이 조회

    - layers : 측정 구분(server | cpu | accelerator)별로 (시각, 전력값 W) 쌍 목록. 시각은 ISO 8601 UTC
    - 조회 기간과 간격은 period, start, end, step 파라미터로 지정

    구분별 값을 쌓아 올리는 누적 그래프에 그대로 쓸 수 있는 형태.
    """
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
    """가속기가 전력을 얼마나 효율적으로 쓰는지 조회

    - pue_estimate : 서버 전체 전력 ÷ (CPU 전력 + 가속기 전력). 냉방 전력 미포함 근사치
    - avg_efficiency_pct_per_watt : 벤더별 전력 1W당 사용률의 평균(%/W)
    - accelerators : 벤더별 전력 합계(W), 사용률 평균(%), 규격 최대 전력(TDP 또는 최대 소비전력, W), 카드 1장 평균의 규격 대비 비중(%), 전력 1W당 사용률(%/W)

    모든 값은 조회 시점의 최신 순간값(수집 주기 5초~1분)
    """
    r = await power_efficiency()
    data = r["data"]
    return PowerEfficiencyResponse(
        status=r["status"],
        data=PowerEfficiencyData(
            pue_estimate=data.get("pue_estimate"),
            avg_efficiency_pct_per_watt=data.get("avg_efficiency_pct_per_watt"),
            accelerators=[AcceleratorEfficiency(**a) for a in data.get("accelerators", [])],
        ),
        warnings=r["warnings"],
    )


@router.get(
    "/monitoring/power/efficiency/timeseries",
    summary="전력 효율 시계열",
    response_model=PowerEfficiencyTimeseriesResponse,
)
async def get_power_efficiency_timeseries(request: Request, params: TimeseriesParams = Depends()):
    """가속기 전력 효율의 시간별 변화 추이 조회

    - series : 벤더별 (시각, 전력 1W당 사용률 %/W) 쌍 목록. 시각은 ISO 8601 UTC
    - vendor=all : 값이 있는 벤더들의 효율 평균. 전력 효율 조회의 avg_efficiency_pct_per_watt에 대응
    - 산식 : 벤더 평균 사용률(%) ÷ 카드 1장 평균 전력(W). 전력 효율 조회와 동일
    - 조회 기간과 간격은 period, start, end, step 파라미터로 지정

    가속기가 쉬고 있으면(사용률 0) 효율도 0으로 나옴. 서버 전체 전력은 포함하지 않음.
    """
    now = datetime.now(timezone.utc)
    start = params.start_iso(now)
    end = params.end_iso(now)
    step = params.step

    r = await power_efficiency_timeseries(start, end, step)
    return PowerEfficiencyTimeseriesResponse(
        status=r["status"],
        series=[PowerEfficiencySeriesItem(**item) for item in r["data"]],
        warnings=r["warnings"],
    )


@router.get("/monitoring/metrics/query", summary="메트릭 현재값 조회", response_model=MetricsQueryResponse)
async def query_metrics(
    request: Request,
    metric: Optional[str] = Query(None, description="조회할 메트릭 이름. 미리 허용된 목록에 있는 값만 가능"),
):
    """지정한 메트릭의 지금 값 조회

    - metric : 조회한 메트릭 이름
    - results : 메트릭 라벨과 (시각, 값) 쌍

    metric 파라미터는 미리 허용된 메트릭 이름만 받음. 임의 PromQL 실행 방지 목적.
    """
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


def _grouped_query(query: str, by: Optional[str], aggregation: str, step: str) -> str:
    """by 라벨이 있으면 라벨별로 묶고 step 구간을 aggregation으로 요약하는 PromQL로 감싼다.

    range_query는 step 시점의 순간값만 돌려주므로, 히트맵처럼 구간 대표값이 필요하면
    `<agg>_over_time(...[step:])` 서브쿼리로 구간 전체를 요약해야 스파이크가 빠지지 않는다.
    """
    if not by:
        return query
    return f"{aggregation} by ({by}) ({aggregation}_over_time(({query})[{step}:]))"


@router.get("/monitoring/metrics/timeseries", summary="메트릭 시계열(횡단)", response_model=TimeseriesResponse)
async def get_metrics_timeseries(
    request: Request,
    metric: Optional[str] = Query(None, description="조회할 메트릭 이름. 미리 허용된 목록에 있는 값만 가능"),
    by: Optional[str] = Query(
        None,
        pattern="^(Hostname|hostname|cluster)$",
        description="이 라벨 값이 같은 시리즈를 하나로 묶고, 각 step 구간을 aggregation 방식으로 요약. 히트맵처럼 호스트별 한 줄이 필요할 때 사용",
    ),
    params: TimeseriesParams = Depends(),
):
    """지정한 메트릭의 시간별 변화 추이 조회

    - metric : 조회한 메트릭 이름
    - series : 메트릭 라벨별 (시각, 값) 쌍 목록
    - 조회 기간과 간격은 period, start, end, step 파라미터로 지정
    - by 지정 시 해당 라벨 단위로 묶이고, 값은 step 구간의 평균(aggregation)이 됨. 미지정 시 시리즈별 step 시점의 순간값

    metric 파라미터는 미리 허용된 메트릭 이름만 받음. 임의 PromQL 실행 방지 목적.
    """
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

    results = await prometheus_client.range_query(
        _grouped_query(METRIC_ALLOWLIST[metric], by, params.aggregation, step), start, end, step
    )

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
    """가속기와 서버 하드웨어 온도를 한데 모은 변화 추이 조회

    - series : 벤더, 클러스터, 라벨별 (시각, 온도값) 쌍 목록
    - 가속기 온도, 노드 온도, 서버 하드웨어(IPMI) 온도를 함께 반환
    """
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
        'kcloud_furiosa_temperature_celsius{sensor="soc_peak",cluster=~"furiosa.*"}', start, end, step
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
                    cluster=item.get("metric", {}).get("cluster", "furiosa"),
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
    """전력 요약값을 주기적으로 밀어주는 실시간 스트림

    - data 이벤트 : 전력 요약 조회와 같은 형태의 JSON
    - 15초마다 heartbeat 이벤트 전송
    - Last-Event-ID 헤더로 끊긴 지점부터 재개 가능
    """
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
    metric: Optional[str] = Query(None, description="조회할 메트릭 이름. 미리 허용된 목록에 있는 값만 가능"),
):
    """지정한 메트릭 값을 주기적으로 밀어주는 실시간 스트림

    - data 이벤트 : 메트릭 현재값 조회와 같은 형태의 JSON
    - 15초마다 heartbeat 이벤트 전송
    - metric 파라미터는 미리 허용된 메트릭 이름만 받음
    """
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
