"""timeseries by 파라미터 자체 점검: 미지정 시 기존 쿼리 그대로, 지정 시 라벨 묶음 + 구간 평균."""
import asyncio

from app.api.v2 import monitoring as mon
from app.api.v2.deps import TimeseriesParams
from app.schemas.monitoring import METRIC_ALLOWLIST


def _run(by):
    calls = []

    async def fake_range_query(query, start, end, step):
        calls.append((query, step))
        return [{"metric": {"Hostname": "gpu-01"}, "values": [[1757000000, "37"], [1757003600, "44"]]}]

    orig = mon.prometheus_client.range_query
    mon.prometheus_client.range_query = fake_range_query
    try:
        params = TimeseriesParams(period="24h", start=None, end=None, step="1h", aggregation="avg")
        out = asyncio.run(mon.get_metrics_timeseries(None, metric="gpu_utilization", by=by, params=params))
    finally:
        mon.prometheus_client.range_query = orig
    return out, calls


def test_without_by_keeps_raw_query():
    out, calls = _run(None)
    assert calls == [(METRIC_ALLOWLIST["gpu_utilization"], "1h")]
    assert out.status == "success" and out.series[0].metric == {"Hostname": "gpu-01"}


def test_with_by_groups_and_averages_each_step():
    out, calls = _run("Hostname")
    q, step = calls[0]
    assert q == 'avg by (Hostname) (avg_over_time((DCGM_FI_DEV_GPU_UTIL{cluster="l40s"})[1h:]))', q
    # 응답 모양은 그대로: series 한 항목 = 히트맵 한 줄, values = 칸
    assert [v[1] for v in out.series[0].values] == ["37", "44"]
    assert out.series[0].values[0][0].startswith("2025-09-04T")
