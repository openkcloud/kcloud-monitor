"""여러 라우터가 함께 쓰는 쿼리 파라미터 모음.

- PaginationParams: 목록 API 공통 (limit/offset/정렬/검색)
- WorkloadFilterParams: 워크로드·OpenStack 전용 (페이징 + 도메인 필터)
- TimeseriesParams: 시계열 API (기간/구간/집계)

FastAPI Depends() 로 주입하면 쿼리 파라미터를 자동 해석.
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Query

# period 문자열("1h"/"30m"/"7d" …) → timedelta 단위 매핑
_PERIOD_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
_MAX_PERIOD = timedelta(days=10)  # Prometheus 보관 기간 10일. 그보다 긴 조회는 무의미


def parse_period(period: str) -> timedelta:
    """"1h"/"30m"/"7d" 형태를 timedelta로 변환.

    해석 실패 시 1시간으로 대체. 보관 기간인 10일을 상한으로 적용.
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
        limit: int = Query(100, ge=1, le=1000, description="한 번에 받을 최대 개수 (1~1000)"),
        offset: int = Query(0, ge=0, description="건너뛸 개수. 페이지 이동용"),
        sort_by: Optional[str] = Query(None, description="정렬 기준 필드 이름. 자원마다 지원 필드 다름"),
        sort_order: str = Query("asc", pattern="^(asc|desc)$", description="정렬 방향 (asc | desc)"),
        search: Optional[str] = Query(None, description="이름 부분 일치 검색어 (대소문자 무시)"),
    ):
        self.limit = limit
        self.offset = offset
        self.sort_by = sort_by
        self.sort_order = sort_order
        self.search = search


class WorkloadFilterParams:
    """워크로드와 OpenStack 목록 조회용. 페이징과 필터."""

    def __init__(
        self,
        limit: int = Query(100, ge=1, le=1000, description="한 번에 받을 최대 개수 (1~1000)"),
        offset: int = Query(0, ge=0, description="건너뛸 개수. 페이지 이동용"),
        sort_by: Optional[str] = Query(None, description="정렬 기준 필드 이름. 자원마다 지원 필드 다름"),
        sort_order: str = Query("asc", pattern="^(asc|desc)$", description="정렬 방향 (asc | desc)"),
        search: Optional[str] = Query(None, description="이름 부분 일치 검색어 (대소문자 무시)"),
        project: Optional[str] = Query(None, description="OpenStack 프로젝트 이름 필터"),
        namespace: Optional[str] = Query(None, description="Kubernetes 네임스페이스 필터"),
        service_name: Optional[str] = Query(None, description="서비스(애플리케이션) 이름 부분 일치 필터"),
        workload_type: Optional[str] = Query(None, description="워크로드 종류 필터 (예: ReplicaSet, StatefulSet, DaemonSet, Job). 대소문자 무시"),
        status: Optional[str] = Query(None, description="상태 필터 (예: Running, Pending). 대소문자 무시"),
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
        period: str = Query("1h", description="조회 기간 (예: 30m, 1h, 7d). start 미지정 시 적용, 최대 10일, 해석 불가 시 1h"),
        start: Optional[str] = Query(None, description="시작 시각 (ISO 8601). 미지정 시 현재 시각에서 period 만큼 이전"),
        end: Optional[str] = Query(None, description="종료 시각 (ISO 8601). 미지정 시 현재 시각"),
        step: str = Query("5m", description="데이터 점 간격 (예: 1m, 5m, 1h)"),
        aggregation: str = Query("avg", pattern="^(avg|min|max|sum)$", description="구간 집계 방식 (avg | min | max | sum)"),
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
