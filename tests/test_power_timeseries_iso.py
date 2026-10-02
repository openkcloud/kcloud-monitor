"""전력 시계열 시각이 유닉스 초가 아닌 ISO 8601 UTC로 나오는지 점검."""
import asyncio

from app.services import power


def test_power_timeseries_uses_iso_time():
    async def fake_range(query, start, end, step):
        # 2026-10-02 실측 응답의 한 점: 1790917530.481 = 05:05:30.481 UTC (한국 14:05:30)
        return [{"metric": {}, "values": [[1790917530.481, "2179"], ["bad", "1"]]}]

    orig = power.prometheus_client.range_query
    power.prometheus_client.range_query = fake_range
    try:
        r = asyncio.run(power.power_timeseries("s", "e", "5m"))
    finally:
        power.prometheus_client.range_query = orig

    server = next(l for l in r["data"] if l["layer"] == "server")
    assert server["values"] == [("2026-10-02T05:05:30.481000+00:00", "2179")]  # 잘못된 점은 버림
