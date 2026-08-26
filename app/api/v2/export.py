"""
KCloud Monitor v2 — Export (3개).

전력/메트릭 데이터 CSV·JSON 내보내기, 요약 리포트(pdf)는 의존성 미도입으로 NOT_CONFIGURED 고정.
데이터소스: Prometheus range_query만 사용(외부 의존 없음).
포맷: csv | json만 지원 — requirements.txt에 pandas/openpyxl/pyarrow 없음(§5-D 결정 전까지
excel/parquet/pdf 등은 400 UNSUPPORTED_FORMAT).
설계: sample_api.md Export 카테고리 / .omc/plans/remaining-domains-plan.md §1-A.
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
    format: str = Query("csv", description="내보내기 포맷 (csv|json)"),
    params: TimeseriesParams = Depends(),
):
    """전력 3계층(server/cpu/accelerator) 시계열을 timestamp,node,layer,watts 행으로 내보낸다.

    format=csv: text/csv 파일 스트림(Content-Disposition: attachment).
    format=json: 기존 응답 봉투(status/observed_at/is_stale/warnings) + rows 배열.
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
    format: str = Query("csv", description="내보내기 포맷 (csv|json)"),
    metric: Optional[str] = Query(None, description="대상 메트릭(허용 목록)"),
    params: TimeseriesParams = Depends(),
):
    """지정 메트릭(허용 목록)의 시계열을 timestamp,labels,value 행으로 내보낸다.

    임의 PromQL 주입을 막기 위해 metric은 EXPORT_METRIC_ALLOWLIST에 있는 값만 허용한다.
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
    report_type: str = Query("daily", pattern="^(daily|weekly|monthly)$", description="리포트 주기"),
):
    """운영 요약 리포트(pdf) — pdf 생성 의존성(reportlab 등) 미도입으로 항상 NOT_CONFIGURED.

    §5-D(의존성 추가 승인) 결정 전까지는 status="partial" + warnings=["NOT_CONFIGURED"]를 반환한다.
    """
    return ReportExportResponse(
        status="partial",
        data=None,
        report_type=report_type,
        warnings=["NOT_CONFIGURED"],
    )
