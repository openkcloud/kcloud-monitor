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

from app.api.v2.accelerators import count_throttle, throttle_series
from app.api.v2.deps import TimeseriesParams, parse_period
from app.schemas.accelerators import ThrottlingSeriesItem, ThrottlingSummary, ThrottlingTimeseriesResponse
from app.schemas.monitoring import (
    METRIC_ALLOWLIST,
    AcceleratorEfficiency,
    AcceleratorUtilizationData,
    AcceleratorUtilizationResponse,
    AcceleratorUtilizationTimeseriesResponse,
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
    HostUtilization,
    HostUtilizationSeriesItem,
    TimeseriesResponse,
    UtilizationSeriesItem,
    VendorUtilization,
)
from app.services.cluster_discovery import cluster_discovery
from app.services.power import (
    accelerator_utilization,
    accelerator_utilization_timeseries,
    power_breakdown,
    power_efficiency,
    power_efficiency_timeseries,
    power_summary,
    power_timeseries,
)
from app.services.prometheus import prometheus_client

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/monitoring/overview", summary="전체 인프라 현황(KPI)", response_model=OverviewResponse)
async def get_overview(request: Request):
    """전체 인프라 현황을 한 화면용 요약값으로 조회

    - clusters : 전체, 관리, 서비스 클러스터 수
    - nodes : 전체, 물리, 가상, 정상, 비정상 노드 수
    - accelerator_count : 가속기 클러스터의 메트릭 수집 대상 수 (카드 수와 다를 수 있음)
    - healthy_count : 그중 응답 중인(up) 수집 대상 수
    - avg_temperature : 클러스터별(l40s | furiosa | rebellions) 평균 가속기 온도(°C), 미수집 시 null

    리벨리온 온도는 1000배 보정값이며 REBELLIONS_SCALE_CORRECTED_X1000 경고 동반.
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
    """인프라 전체 전력을 측정 구분별로 나눈 합계 조회

    - server_total_watts : 서버 총 전력(W), IPMI 실측
    - cpu_total_watts : CPU 전력(W), Kepler 실측
    - accelerator_total_watts : 가속기 전력 합계(W)
    - accelerator_by_vendor : 벤더별(nvidia | furiosa | rebellions) 가속기 전력 합계(W)
    - other_watts : 서버 총 전력에서 CPU와 가속기를 뺀 나머지 전력(W)

    미수집 항목은 null.
    """
    r = await power_summary()
    return PowerSummaryResponse(
        status=r["status"],
        data=PowerSummaryData(**r["data"]),
        warnings=r["warnings"],
    )


@router.get("/monitoring/power/breakdown", summary="기준별 전력 분포", response_model=PowerBreakdownResponse)
async def get_power_breakdown(
    request: Request,
    dimension: str = Query("vendor", description="나누는 기준 (vendor | cluster | node | accelerator). vendor: 벤더별, cluster: 클러스터별, node: 노드별, accelerator: 카드별"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$", description="정렬 방향 (desc | asc). desc: 큰 값 먼저"),
):
    """선택한 기준(벤더, 클러스터, 노드, 카드)별 전력 조회

    - dimension : 적용된 기준(vendor | cluster | node | accelerator)
    - items : 기준값 이름(key), 전력(W), 측정 구분(layer: server | cpu | accelerator)

    전력 큰 순 정렬이 기본이며 sort_order=asc 로 작은 순. 전력 값이 없는 항목은 맨 뒤. node 기준은 노드마다 서버 전력(server)과 CPU 전력(cpu) 항목이 따로 나옴. 알 수 없는 기준이면 UNKNOWN_DIMENSION 경고와 빈 목록 반환.
    """
    r = await power_breakdown(dimension)
    sign = -1 if sort_order == "desc" else 1
    items = sorted(r["data"], key=lambda i: (i["watts"] is None, sign * (i["watts"] or 0.0)))
    return PowerBreakdownResponse(
        status=r["status"],
        dimension=dimension,
        items=[PowerBreakdownItem(**i) for i in items],
        warnings=r["warnings"],
    )


@router.get("/monitoring/power/timeseries", summary="전체 전력 시계열", response_model=PowerTimeseriesResponse)
async def get_power_timeseries(request: Request, params: TimeseriesParams = Depends()):
    """인프라 전체 전력의 측정 구분별 변화 추이 조회

    - layers : 측정 구분(server | cpu | accelerator)별 (시각, 전력 W) 쌍 목록. 시각은 ISO 8601 UTC

    누적 그래프에 바로 쓰는 형태. 조회 기간과 간격은 period, start, end, step 으로 지정.
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


@router.get("/monitoring/power/efficiency", summary="가속기 전력 효율", response_model=PowerEfficiencyResponse)
async def get_power_efficiency(request: Request):
    """가속기의 전력 대비 사용률 효율 조회

    - pue_estimate : 서버 전체 전력 ÷ (CPU 전력 + 가속기 전력). 냉방 전력 미포함 근사치
    - avg_efficiency_pct_per_watt : 벤더별 1W당 사용률의 평균(%/W)
    - accelerators : 벤더별 전력 합계(W), 평균 사용률(%), 규격 최대 전력(W), 카드 1장 평균의 규격 대비 비중(%), 1W당 사용률(%/W)

    모든 값은 조회 시점 최신값 (수집 주기 5초~1분).
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
    summary="가속기 전력 효율 시계열",
    response_model=PowerEfficiencyTimeseriesResponse,
)
async def get_power_efficiency_timeseries(request: Request, params: TimeseriesParams = Depends()):
    """가속기 전력 효율의 변화 추이 조회

    - series : 벤더별 (시각, 1W당 사용률 %/W) 쌍 목록. 시각은 ISO 8601 UTC
    - vendor=all 항목 : 값이 있는 벤더들의 효율 평균

    산식은 벤더 평균 사용률(%) ÷ 카드 1장 평균 전력(W). 사용률 0이면 효율 0. 서버 전체 전력은 미포함. 조회 기간과 간격은 period, start, end, step 으로 지정.
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


@router.get(
    "/monitoring/accelerators/utilization",
    summary="가속기 사용률",
    response_model=AcceleratorUtilizationResponse,
)
async def get_accelerator_utilization(
    request: Request,
    by: Optional[str] = Query(None, pattern="^node$", description="node 지정 시 호스트별 목록(hosts)도 반환"),
    limit: Optional[int] = Query(None, ge=1, description="hosts 최대 개수. 미지정 시 전체"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$", description="hosts 정렬 방향 (desc | asc). desc: 사용률 높은 값 먼저"),
):
    """전체 가속기의 현재 사용률 조회

    - avg_utilization_pct : 전체 카드 사용률 평균(%). 벤더 구분 없이 카드 1장씩 같은 비중
    - card_count : 값이 수집된 카드 수
    - vendors : 벤더별 카드 수, 평균 사용률(%)
    - hosts : 호스트별 벤더, 카드 수, 평균 사용률(%) (by=node 일 때만)

    hosts 는 sort_order(기본 desc) 순으로 limit 개까지. 모든 값은 조회 시점 최신값 (수집 주기 5초~1분).
    """
    r = await accelerator_utilization(by=by, limit=limit, sort_order=sort_order)
    data = r["data"]
    return AcceleratorUtilizationResponse(
        status=r["status"],
        data=AcceleratorUtilizationData(
            avg_utilization_pct=data.get("avg_utilization_pct"),
            card_count=data.get("card_count", 0),
            vendors=[VendorUtilization(**v) for v in data.get("vendors", [])],
            hosts=[HostUtilization(**h) for h in data.get("hosts", [])],
        ),
        warnings=r["warnings"],
    )


@router.get(
    "/monitoring/accelerators/utilization/timeseries",
    summary="가속기 사용률 시계열",
    response_model=AcceleratorUtilizationTimeseriesResponse,
)
async def get_accelerator_utilization_timeseries(
    request: Request,
    by: Optional[str] = Query(None, pattern="^node$", description="node 지정 시 호스트별 시계열(hosts)도 반환"),
    params: TimeseriesParams = Depends(),
):
    """전체 가속기 사용률의 변화 추이 조회

    - series : 벤더별 (시각, 평균 사용률 %) 쌍 목록. 시각은 ISO 8601 UTC
    - vendor=all 항목 : 벤더 구분 없이 카드 1장씩 같은 비중으로 계산한 평균
    - hosts : 호스트별 벤더와 (시각, 평균 사용률 %) 쌍 목록 (by=node 일 때만)

    값은 각 step 시점의 순간값 평균이라 step 사이의 짧은 변화는 미반영. 조회 기간과 간격은 period, start, end, step 으로 지정.
    """
    now = datetime.now(timezone.utc)
    r = await accelerator_utilization_timeseries(params.start_iso(now), params.end_iso(now), params.step, by=by)
    return AcceleratorUtilizationTimeseriesResponse(
        status=r["status"],
        series=[UtilizationSeriesItem(**s) for s in r["data"]["series"]],
        hosts=[HostUtilizationSeriesItem(**h) for h in r["data"]["hosts"]],
        warnings=r["warnings"],
    )


@router.get("/monitoring/metrics/query", summary="메트릭 현재값", response_model=MetricsQueryResponse)
async def query_metrics(
    request: Request,
    metric: Optional[str] = Query(None, description="조회할 메트릭 이름. 허용 목록에 있는 값만"),
):
    """허용된 메트릭 한 개의 현재 값 조회

    - metric : 조회한 메트릭 이름
    - results : 시리즈별 메트릭 라벨(metric)과 현재값(value). value 는 (Unix 시각(초), 값 문자열)

    metric 은 허용 목록에 있는 이름만 가능하며 없으면 400. 결과가 없으면 NO_DATA 경고 반환.
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


@router.get("/monitoring/metrics/timeseries", summary="메트릭 시계열", response_model=TimeseriesResponse)
async def get_metrics_timeseries(
    request: Request,
    metric: Optional[str] = Query(None, description="조회할 메트릭 이름. 허용 목록에 있는 값만"),
    by: Optional[str] = Query(
        None,
        pattern="^(Hostname|hostname|cluster)$",
        description="묶을 라벨 (Hostname | hostname | cluster). 지정 시 라벨 값이 같은 시리즈를 하나로 묶고 각 step 구간을 aggregation 방식으로 요약",
    ),
    params: TimeseriesParams = Depends(),
):
    """허용된 메트릭 한 개의 변화 추이 조회

    - metric : 조회한 메트릭 이름
    - series : 메트릭 라벨(metric)별 (시각, 값) 쌍 목록(values). 시각은 ISO 8601 UTC

    by 지정 시 그 라벨 단위로 묶고 aggregation 방식으로 요약, 미지정 시 시리즈별 순간값. metric 은 허용 목록에 있는 이름만 가능하며 없으면 400. 조회 기간과 간격은 period, start, end, step 으로 지정.
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
    summary="가속기 온도 시계열",
    response_model=TemperatureTimeseriesResponse,
)
async def get_temperature_timeseries(request: Request, params: TimeseriesParams = Depends()):
    """전체 가속기 온도의 변화 추이 조회

    - series : 장치별 벤더(nvidia | furiosa | rebellions), 클러스터, 메트릭 라벨, (시각, 온도 °C) 쌍 목록. 시각은 ISO 8601 UTC

    리벨리온 온도는 1000배 보정값이며 REBELLIONS_SCALE_CORRECTED_X1000 경고 동반. 벤더별 미수집 시 NO_DATA_NVIDIA, NO_DATA_FURIOSA, NO_DATA_REBELLIONS 경고. 조회 기간과 간격은 period, start, end, step 으로 지정.
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


@router.get(
    "/monitoring/throttling/timeseries",
    summary="가속기 쓰로틀링 시계열",
    response_model=ThrottlingTimeseriesResponse,
)
async def get_throttling_timeseries(
    request: Request,
    cluster: Optional[str] = Query(None, description="클러스터 이름 필터. 미지정 시 전체"),
    node: Optional[str] = Query(None, description="노드 이름 필터. 미지정 시 전체"),
    acc_id: Optional[str] = Query(None, description="가속기 ID 필터. 미지정 시 전체"),
    detail: bool = Query(False, description="true 지정 시 카드별 시계열(series)도 반환. 기본은 summary 만"),
    params: TimeseriesParams = Depends(),
):
    """가속기가 스스로 속도를 낮춘(쓰로틀링) 시점과 횟수 조회

    - summary : 대상 카드 수, 쓰로틀링 발생 카드 수, 시작 횟수 합계, 지속 시간 합계(분), 판정 간격(초)
    - series : 카드별 벤더, 클러스터, 노드, 가속기 ID, (시각, 1 | 0) 목록, 시작 횟수, 지속 시간(분) (detail=true 일 때만)

    판정 기준은 2분 평균 사용률 90% 이상, 최근 10분 최고 온도 기준 이상, 전력 15분 최대의 85% 미만. 값이 빠진 시각은 점 없음. step 이 길면 짧은 쓰로틀링 누락 가능. 대상이 없으면 summary 는 null 이고 UNKNOWN_CLUSTER 또는 NO_DATA 경고 반환.
    """
    now = datetime.now(timezone.utc)
    start, end, step = params.start_iso(now), params.end_iso(now), params.step
    step_seconds = parse_period(step).total_seconds()

    series: list[ThrottlingSeriesItem] = []
    clusters = await cluster_discovery.get_clusters()
    if cluster and cluster not in clusters:
        return ThrottlingTimeseriesResponse(status="partial", warnings=["UNKNOWN_CLUSTER"])
    for name, info in clusters.items():
        if not info.vendor or (cluster and name != cluster):
            continue
        for s in await throttle_series(info.vendor, name, start, end, step, node=node, acc_id=acc_id):
            events, minutes = count_throttle(s["values"], step_seconds)
            series.append(ThrottlingSeriesItem(
                vendor=info.vendor, cluster=name, throttle_events=events, throttled_minutes=minutes, **s
            ))

    if not series:
        return ThrottlingTimeseriesResponse(status="partial", warnings=["NO_DATA"])
    summary = ThrottlingSummary(
        cards_total=len(series),
        cards_throttled=sum(1 for s in series if s.throttle_events),
        throttle_events=sum(s.throttle_events for s in series),
        throttled_minutes=sum(s.throttled_minutes for s in series),
        step_seconds=step_seconds,
    )
    return ThrottlingTimeseriesResponse(status="success", series=series if detail else [], summary=summary)


@router.get("/monitoring/stream/power", summary="실시간 전력 스트림(SSE)")
async def stream_power(request: Request):
    """전력 요약값을 주기적으로 보내는 SSE 스트림

    - power 이벤트 : status, warnings, observed_at, data(server_total_watts, cpu_total_watts, accelerator_total_watts, accelerator_by_vendor, other_watts, 단위 W)
    - heartbeat 이벤트 : 15초마다 observed_at 전송
    - error 이벤트 : 조회 실패 시 error="poll_failed", observed_at 전송 후 스트림 유지

    15초 간격으로 전송. 각 이벤트에 연결 이후 1부터 증가하는 id 포함.
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
    metric: Optional[str] = Query(None, description="조회할 메트릭 이름. 허용 목록에 있는 값만"),
):
    """허용된 메트릭 값을 주기적으로 보내는 SSE 스트림

    - metric 이벤트 : status, metric(메트릭 이름), observed_at, results(시리즈별 라벨 metric 과 value: Unix 시각(초), 값 문자열)
    - heartbeat 이벤트 : 15초마다 observed_at 전송
    - error 이벤트 : 조회 실패 시 error="poll_failed", observed_at 전송 후 스트림 유지

    15초 간격으로 전송. metric 은 허용 목록에 있는 이름만 가능하며 없으면 400.
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
