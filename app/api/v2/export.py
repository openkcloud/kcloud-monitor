"""
KCloud Monitor v2 — Export (3개).

전력/메트릭 데이터와 요약 리포트 내보내기. 포맷: csv | excel | parquet | pdf(리포트).
v1 익스포터 구현(app/services/exporters, git 이력 보존)을 실제 구현 시 재사용 가능.
설계: sample_api.md Export 카테고리.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from app.api.v2._stub import stub
from app.api.v2.deps import timeseries_params

router = APIRouter()


@router.get("/export/power", summary="전력 데이터 내보내기")
async def export_power(
    request: Request,
    format: str = Query("csv", pattern="^(csv|excel|parquet)$", description="내보내기 포맷"),
    params: dict = Depends(timeseries_params),
):
    """전력 데이터 파일 내보내기 — 기간/집계 파라미터 적용."""
    merged = dict(params)
    merged["format"] = format
    return stub(
        request,
        "전력 데이터 내보내기(csv/excel/parquet)",
        sources=("Mimir(range query)", "v1 exporters 재사용 예정"),
        params=merged,
    )


@router.get("/export/metrics", summary="메트릭 데이터 내보내기")
async def export_metrics(
    request: Request,
    format: str = Query("csv", pattern="^(csv|excel|parquet)$", description="내보내기 포맷"),
    metric: Optional[str] = Query(None, description="대상 메트릭(허용 목록)"),
    params: dict = Depends(timeseries_params),
):
    """메트릭 데이터 파일 내보내기."""
    merged = dict(params)
    merged["format"] = format
    if metric:
        merged["metric"] = metric
    return stub(
        request,
        "메트릭 데이터 내보내기(csv/excel/parquet)",
        sources=("Mimir(range query)",),
        params=merged,
    )


@router.get("/export/report", summary="요약 리포트 생성")
async def export_report(
    request: Request,
    report_type: str = Query("daily", pattern="^(daily|weekly|monthly)$", description="리포트 주기"),
):
    """운영 요약 리포트(PDF) — 일간/주간/월간 전력·사용률·이상 요약."""
    return stub(
        request,
        "요약 리포트 생성(pdf, daily/weekly/monthly)",
        sources=("Mimir(집계 질의)", "v1 pdf_exporter 재사용 예정"),
        params={"report_type": report_type},
    )
