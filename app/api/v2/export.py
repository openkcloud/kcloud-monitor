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
    format: str = Query("csv", description="응답 형식 (csv | json)"),
    params: TimeseriesParams = Depends(),
):
    """기간 내 전력 측정값을 CSV 또는 JSON 으로 내보내기

    - 행 : timestamp(측정 시각, ISO 8601 UTC), node(노드 이름), layer(측정 구분), watts(전력 W)
    - layer : server(IPMI 서버 총전력) | cpu(Kepler CPU 전력) | accelerator:벤더(가속기 전력)
    - format=csv : 첨부 파일(power_시작_종료.csv)로 내려받기
    - format=json : data.rows 배열로 반환

    조회 기간과 간격은 period, start, end, step 으로 지정. csv, json 외 형식은 400. 계층별 데이터가 없으면 NO_DATA_SERVER_POWER, NO_DATA_CPU_POWER, NO_DATA_ACCELERATOR 경고.
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
    format: str = Query("csv", description="응답 형식 (csv | json)"),
    metric: Optional[str] = Query(None, description="조회할 메트릭 이름. 허용 목록에 있는 값만"),
    params: TimeseriesParams = Depends(),
):
    """허용된 메트릭의 기간 내 값을 CSV 또는 JSON 으로 내보내기

    - 행 : timestamp(측정 시각, ISO 8601 UTC), labels(원본 메트릭 라벨), value(값)
    - format=csv : 첨부 파일(metrics_메트릭_시작_종료.csv)로 내려받기. labels 는 키=값;키=값 문자열
    - format=json : data.metric 과 data.rows 배열로 반환

    metric 은 미리 허용된 이름만 가능하며 그 외는 400 INVALID_METRIC. csv, json 외 형식은 400.
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


@router.get("/export/report", summary="운영 요약 리포트 생성", response_model=ReportExportResponse)
async def export_report(
    request: Request,
    report_type: str = Query("daily", pattern="^(daily|weekly|monthly)$", description="리포트 주기 (daily | weekly | monthly)"),
):
    """일간, 주간, 월간 운영 요약 리포트 생성

    - report_type : 리포트 주기(daily | weekly | monthly)
    - 리포트 생성 기능 미설정 상태로 status="partial", data=null, NOT_CONFIGURED 경고 반환
    """
    return ReportExportResponse(
        status="partial",
        data=None,
        report_type=report_type,
        warnings=["NOT_CONFIGURED"],
    )
