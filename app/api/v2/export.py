"""측정 데이터 내보내기 라우터

지원 포맷은 csv와 json. 그 외 포맷 요청은 400 UNSUPPORTED_FORMAT 반환.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from app.api.v2.deps import TimeseriesParams
from app.schemas.export import (
    EXPORT_METRIC_ALLOWLIST,
    MetricExportData,
    MetricExportResponse,
    MetricExportRow,
    PowerExportData,
    PowerExportResponse,
    PowerExportRow,
    ReportExportResponse,
)
from app.services.exporters import metric_export_rows, power_export_rows, rows_to_csv

router = APIRouter()

_SUPPORTED_FORMATS = ("csv", "json")


def _check_format(format: str) -> None:
    """csv|json 이외 포맷 요청 시 400. excel/parquet 등은 의존성 미도입(§5-D)으로 차단."""
    if format not in _SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"UNSUPPORTED_FORMAT: csv|json만 지원 (요청값: {format})",
        )


def _safe_filename_part(value: str) -> str:
    """Content-Disposition 파일명에 부적합한 문자(콜론 등) 치환 — ISO 8601 타임스탬프 대응."""
    return value.replace(":", "-").replace("/", "-")


@router.get("/export/power", summary="전력 데이터 내보내기")
async def export_power(
    request: Request,
    format: str = Query("csv", description="내보내기 포맷: csv | json"),
    params: TimeseriesParams = Depends(),
):
    """전력 측정값을 파일로 내보내기

    - 행 구성 : 시각, 노드 이름, 측정 구분(server | cpu | accelerator), 전력(W)
    - format=csv : CSV 파일로 바로 내려받기
    - format=json : JSON 응답 본문의 rows 배열로 반환
    """
    _check_format(format)

    now = datetime.now(timezone.utc)
    start = params.start_iso(now)
    end = params.end_iso(now)
    step = params.step

    rows, warnings = await power_export_rows(start, end, step)
    status = "partial" if warnings else "success"

    if format == "json":
        return PowerExportResponse(
            status=status,
            data=PowerExportData(rows=[PowerExportRow(**r) for r in rows]),
            warnings=warnings,
        )

    csv_body = rows_to_csv(rows, fieldnames=["timestamp", "node", "layer", "watts"])
    filename = f"power_{_safe_filename_part(start)}_{_safe_filename_part(end)}.csv"
    return Response(
        content=csv_body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/metrics", summary="메트릭 데이터 내보내기")
async def export_metrics(
    request: Request,
    format: str = Query("csv", description="내보내기 포맷: csv | json"),
    metric: Optional[str] = Query(None, description="내보낼 메트릭 이름. 미리 허용된 목록에 있는 값만 가능"),
    params: TimeseriesParams = Depends(),
):
    """지정한 메트릭 값을 파일로 내보내기

    - 행 구성 : 시각, 메트릭 라벨, 값
    - format=csv : CSV 파일로 바로 내려받기
    - format=json : JSON 응답 본문의 rows 배열로 반환

    metric 파라미터는 미리 허용된 메트릭 이름만 받음. 임의 PromQL 실행 방지 목적.
    """
    _check_format(format)

    if metric is None or metric not in EXPORT_METRIC_ALLOWLIST:
        raise HTTPException(
            status_code=400,
            detail=f"INVALID_METRIC: 허용 목록 {list(EXPORT_METRIC_ALLOWLIST.keys())} 중에서 선택하세요.",
        )

    now = datetime.now(timezone.utc)
    start = params.start_iso(now)
    end = params.end_iso(now)
    step = params.step

    rows, warnings = await metric_export_rows(EXPORT_METRIC_ALLOWLIST[metric], start, end, step)
    status = "partial" if warnings else "success"

    if format == "json":
        return MetricExportResponse(
            status=status,
            data=MetricExportData(metric=metric, rows=[MetricExportRow(**r) for r in rows]),
            warnings=warnings,
        )

    csv_body = rows_to_csv(rows, fieldnames=["timestamp", "labels", "value"])
    filename = f"metrics_{metric}_{_safe_filename_part(start)}_{_safe_filename_part(end)}.csv"
    return Response(
        content=csv_body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/report", summary="요약 리포트 생성", response_model=ReportExportResponse)
async def export_report(
    request: Request,
    report_type: str = Query("daily", pattern="^(daily|weekly|monthly)$", description="리포트 주기: daily | weekly | monthly"),
):
    """일간, 주간, 월간 운영 요약 리포트 생성

    - report_type : daily | weekly | monthly
    - PDF 생성 라이브러리가 설치되지 않아 status="partial" + warnings=["NOT_CONFIGURED"] 반환
    """
    return ReportExportResponse(
        status="partial",
        data=None,
        report_type=report_type,
        warnings=["NOT_CONFIGURED"],
    )
