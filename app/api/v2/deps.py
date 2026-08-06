"""
v2 공통 쿼리 파라미터 정의 (sample_api.md "공통 사항").

- 목록형 API: list_params — limit/offset/정렬 + 라벨 필터
  (클러스터 범위 경로 /clusters/{cluster}/* 에서는 cluster가 경로 파라미터이므로
   쿼리 필터에서 제외 — 전역 진입점용 global_list_params가 cluster 필터를 추가한다)
- 시계열 API: timeseries_params — period/start/end/step/aggregation
스텁 단계에서는 검증만 수행하고 응답에 echo한다. 실제 구현 시 crud 계층에 전달.
"""
from typing import Optional

from fastapi import Depends, Query


def list_params(
    limit: int = Query(100, ge=1, le=1000, description="최대 반환 수 (max 1000)"),
    offset: int = Query(0, ge=0, description="페이징 오프셋"),
    sort_by: Optional[str] = Query(None, description="정렬 기준 필드(자원별 상이)"),
    sort_order: str = Query("asc", pattern="^(asc|desc)$", description="정렬 방향"),
    project: Optional[str] = Query(None, description="OpenStack 프로젝트 필터"),
    namespace: Optional[str] = Query(None, description="K8s 네임스페이스 필터"),
    service_name: Optional[str] = Query(None, description="논리 서비스/애플리케이션 필터"),
    workload_type: Optional[str] = Query(None, description="deployment | statefulset | job 등"),
    status: Optional[str] = Query(None, description="상태 필터"),
    search: Optional[str] = Query(None, description="텍스트 검색"),
) -> dict:
    values = locals()
    return {k: v for k, v in values.items() if v is not None}


def global_list_params(
    params: dict = Depends(list_params),
    cluster: Optional[str] = Query(None, description="클러스터 필터(전역 목록 기본값: 서비스 클러스터)"),
) -> dict:
    if cluster is not None:
        params = {**params, "cluster": cluster}
    return params


def timeseries_params(
    period: str = Query("1h", description="조회 기간 (start 미지정 시 사용)"),
    start: Optional[str] = Query(None, description="시작 시각 (ISO 8601)"),
    end: Optional[str] = Query(None, description="종료 시각 (ISO 8601, 기본 now)"),
    step: str = Query("5m", description="데이터 포인트 간격"),
    aggregation: str = Query("avg", pattern="^(avg|min|max|sum)$", description="집계 방식"),
) -> dict:
    values = locals()
    return {k: v for k, v in values.items() if v is not None}
