"""호스트맵용 utilization_percent 자체 점검: 호스트 발견에 쓴 사용률 응답이 호스트 평균으로 들어오는지."""
import asyncio

from app.api.v2 import nodes
from app.services.cluster_discovery import ClusterInfo


def _run(util_res):
    async def fake_instant(query):
        return util_res

    orig = nodes.prometheus_client.instant
    nodes.prometheus_client.instant = fake_instant
    try:
        info = ClusterInfo(name="l40s", label_value="l40s", utilization_query='DCGM_FI_DEV_GPU_UTIL{cluster="l40s"}')
        return asyncio.run(nodes._service_cluster_hosts(info, set()))
    finally:
        nodes.prometheus_client.instant = orig


def test_host_utilization_is_average_of_its_cards():
    rows = _run([
        {"metric": {"Hostname": "gpu-01", "UUID": "a"}, "value": [0, "20"]},
        {"metric": {"Hostname": "gpu-01", "UUID": "b"}, "value": [0, "60"]},
        {"metric": {"Hostname": "gpu-02", "UUID": "c"}, "value": [0, "5"]},
    ])
    by_host = {r[0]: r for r in rows}
    # (host, is_physical, accelerator_count, power_watts, utilization_percent)
    assert by_host["gpu-01"][2] == 2 and by_host["gpu-01"][4] == 40.0
    assert by_host["gpu-02"][2] == 1 and by_host["gpu-02"][4] == 5.0


def test_unparseable_value_still_counts_card_but_leaves_util_empty():
    rows = _run([{"metric": {"Hostname": "gpu-01", "UUID": "a"}, "value": [0, "NaN-ish"]}])
    assert rows == [("gpu-01", False, 1, None, None)]
