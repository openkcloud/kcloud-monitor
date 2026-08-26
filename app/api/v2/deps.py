"""
v2 공통 의존성 — 타입 안전 쿼리 파라미터.

- PaginationParams: 목록 API 공통 (limit/offset/정렬/검색)
- WorkloadFilterParams: 워크로드·OpenStack 전용 (페이징 + 도메인 필터)
- TimeseriesParams: 시계열 API (기간/구간/집계)

FastAPI Depends()로 주입하면 Query 파라미터를 자동 해석한다.
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Query

# period 문자열("1h"/"30m"/"7d" …) → timedelta 단위 매핑
_PERIOD_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
_MAX_PERIOD = timedelta(days=10)  # Prometheus retention 10일 — 초과 조회는 무의미


def parse_period(period: str) -> timedelta:
    """"1h"/"30m"/"7d" 형태를 timedelta로 변환.

    파싱 실패 시 1시간으로 폴백, retention(10일) 상한을 적용한다.
    """
    try:
        value = int(period[:-1])
        unit = period[-1]
        if unit not in _PERIOD_UNITS or value <= 0:
            raise ValueError(period)
        delta = timedelta(**{_PERIOD_UNITS[unit]: value})
    except (ValueError, IndexError):
        delta = timedelta(hours=1)
    return min(delta, _MAX_PERIOD)


class PaginationParams:
    """목록 API 공통 페이징·정렬·검색."""

    def __init__(
        self,
        limit: int = Query(100, ge=1, le=1000, description="최대 반환 수 (max 1000)"),
        offset: int = Query(0, ge=0, description="페이징 오프셋"),
        sort_by: Optional[str] = Query(None, description="정렬 기준 필드(자원별 상이)"),
        sort_order: str = Query("asc", pattern="^(asc|desc)$", description="정렬 방향"),
        search: Optional[str] = Query(None, description="텍스트 검색"),
    ):
        self.limit = limit
        self.offset = offset
        self.sort_by = sort_by
        self.sort_order = sort_order
        self.search = search


class WorkloadFilterParams:
    """워크로드·OpenStack 목록 API용 — 페이징 + 도메인 필터."""

    def __init__(
        self,
        limit: int = Query(100, ge=1, le=1000, description="최대 반환 수 (max 1000)"),
        offset: int = Query(0, ge=0, description="페이징 오프셋"),
        sort_by: Optional[str] = Query(None, description="정렬 기준 필드"),
        sort_order: str = Query("asc", pattern="^(asc|desc)$", description="정렬 방향"),
        search: Optional[str] = Query(None, description="텍스트 검색"),
        project: Optional[str] = Query(None, description="OpenStack 프로젝트 필터"),
        namespace: Optional[str] = Query(None, description="K8s 네임스페이스 필터"),
        service_name: Optional[str] = Query(None, description="논리 서비스/애플리케이션 필터"),
        workload_type: Optional[str] = Query(None, description="deployment | statefulset | job 등"),
        status: Optional[str] = Query(None, description="상태 필터"),
    ):
        self.limit = limit
        self.offset = offset
        self.sort_by = sort_by
        self.sort_order = sort_order
        self.search = search
        self.project = project
        self.namespace = namespace
        self.service_name = service_name
        self.workload_type = workload_type
        self.status = status


class TimeseriesParams:
    """시계열 API 공통 기간·구간·집계."""

    def __init__(
        self,
        period: str = Query("1h", description="조회 기간 (start 미지정 시 사용)"),
        start: Optional[str] = Query(None, description="시작 시각 (ISO 8601)"),
        end: Optional[str] = Query(None, description="종료 시각 (ISO 8601, 기본 now)"),
        step: str = Query("5m", description="데이터 포인트 간격"),
        aggregation: str = Query("avg", pattern="^(avg|min|max|sum)$", description="집계 방식"),
    ):
        self.period = period
        self.start = start
        self.end = end
        self.step = step
        self.aggregation = aggregation

    def start_iso(self, now: datetime) -> str:
        """조회 시작 시각(ISO). start가 있으면 그대로, 없으면 period로 계산."""
        if self.start:
            return self.start
        return (now - parse_period(self.period)).isoformat()

    def end_iso(self, now: datetime) -> str:
        """조회 종료 시각(ISO). end가 있으면 그대로, 없으면 now."""
        return self.end or now.isoformat()
