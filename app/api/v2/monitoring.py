"""
KCloud Monitor v2 — Monitoring 횡단 집계 (10개: REST 8 + SSE 2).

여러 클러스터를 가로지르는 전력/메트릭 집계와 실시간 스트리밍(SSE).
v1 WebSocket은 v2에서 SSE로 통일(design_contracts §7: 15초 heartbeat, Last-Event-ID 재개).
데이터소스(구현 예정): Mimir(PromQL 횡단 질의), 전력 귀속 recording rules.
설계: sample_api.md §1.1, §8.1~§8.4, §10.2~§10.3 / 전력 계층 P8, 메트릭 M5.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from app.api.v2._stub import sse_stub, stub
from app.api.v2.deps import timeseries_params

router = APIRouter()


@router.get("/monitoring/overview", summary="전체 시스템 현황(KPI)")
async def get_overview(request: Request):
    """포탈 메인 KPI — 클러스터/노드/가속기/워크로드 수, 전력 합계, (추론 KPI RPS·P99·에러율 예정, A-6). [§1.1]"""
    return stub(
        request,
        "전체 시스템 현황 KPI(overview)",
        sources=("Mimir(PromQL 집계)", "vllm:* recording rules(추론 KPI, A-4 B안)"),
        ref="sample_api.md §1.1",
    )


@router.get("/monitoring/power/summary", summary="전력 요약 [P8]")
async def get_power_summary(request: Request):
    """시스템 전체 전력 요약 — 계층별(물리/노드/가속기/VM/Pod) 합계와 신뢰도 표기. 전력 계층 P8. [§8.1]"""
    return stub(
        request,
        "전력 요약(P8, 계층별 합계·source/power_estimation 표기)",
        sources=("Mimir(physical_host_power_watts, kepler_*, DCGM_*, *_estimated)",),
        ref="sample_api.md §8.1",
    )


@router.get("/monitoring/power/breakdown", summary="전력 분해(다차원)")
async def get_power_breakdown(request: Request, dimension: Optional[str] = Query(None, description="cluster|node|accelerator|namespace|project")):
    """전력 분해 — 클러스터/노드/가속기/네임스페이스/프로젝트 차원별 기여도. [§8.2]"""
    return stub(
        request,
        "전력 분해(다차원 기여도)",
        sources=("Mimir(전력 귀속 recording rules)",),
        ref="sample_api.md §8.2",
        params={"dimension": dimension} if dimension else None,
    )


@router.get("/monitoring/power/timeseries", summary="전력 시계열(횡단)")
async def get_power_timeseries(request: Request, params: dict = Depends(timeseries_params)):
    """시스템 전력 시계열 — 계층 스택 구성용. [§8.3]"""
    return stub(
        request,
        "전력 시계열(횡단)",
        sources=("Mimir(range query)",),
        ref="sample_api.md §8.3",
        params=params,
    )


@router.get("/monitoring/power/efficiency", summary="전력 효율")
async def get_power_efficiency(request: Request):
    """전력 효율 — 사용률 대비 전력, PUE 추정(냉각 계수), 유휴 전력 비중. [§8.4]"""
    return stub(
        request,
        "전력 효율 지표(PUE 추정 포함)",
        sources=("Mimir(전력·사용률 결합 질의)",),
        ref="sample_api.md §8.4",
    )


@router.get("/monitoring/metrics/timeseries", summary="메트릭 시계열(횡단)")
async def get_metrics_timeseries(
    request: Request,
    metric: Optional[str] = Query(None, description="조회할 메트릭 이름(카탈로그 내 허용 목록)"),
    params: dict = Depends(timeseries_params),
):
    """지정 메트릭의 횡단 시계열 — 허용 목록 기반(PromQL 인젝션 방지 계약 유지)."""
    merged = dict(params)
    if metric:
        merged["metric"] = metric
    return stub(
        request,
        "메트릭 시계열(허용 목록 기반)",
        sources=("Mimir(range query)",),
        params=merged,
    )


@router.get("/monitoring/metrics/query", summary="메트릭 즉시 질의 [M5]")
async def query_metrics(
    request: Request,
    metric: Optional[str] = Query(None, description="조회할 메트릭 이름(카탈로그 내 허용 목록)"),
):
    """메트릭 즉시값 질의 — 허용 목록 기반 안전 질의. 메트릭 계층 M5."""
    return stub(
        request,
        "메트릭 즉시 질의(허용 목록 기반)",
        sources=("Mimir(instant query)",),
        params={"metric": metric} if metric else None,
    )


@router.get("/monitoring/temperature/timeseries", summary="온도 시계열(횡단)")
async def get_temperature_timeseries(request: Request, params: dict = Depends(timeseries_params)):
    """가속기/노드/IPMI 온도 통합 시계열."""
    return stub(
        request,
        "온도 시계열(가속기·노드·IPMI 통합)",
        sources=("Mimir(DCGM_FI_DEV_GPU_TEMP, node_hwmon_temp_celsius, ipmi_temperature_celsius)",),
        params=params,
    )


@router.get("/monitoring/stream/power", summary="실시간 전력 스트림(SSE)")
async def stream_power(request: Request):
    """전력 실시간 SSE — REST와 동일 응답 모델의 data 이벤트 + 15초 heartbeat. [§10.2]"""
    return sse_stub(
        request,
        "전력 실시간 스트림(SSE, v1 WebSocket 대체)",
        sources=("Mimir(주기 질의)",),
        ref="sample_api.md §10.2",
    )


@router.get("/monitoring/stream/metrics", summary="실시간 메트릭 스트림(SSE)")
async def stream_metrics(request: Request):
    """메트릭 실시간 SSE — Last-Event-ID 재개 지원 예정. [§10.3]"""
    return sse_stub(
        request,
        "메트릭 실시간 스트림(SSE)",
        sources=("Mimir(주기 질의)",),
        ref="sample_api.md §10.3",
    )
