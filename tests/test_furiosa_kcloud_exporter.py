"""퓨리오사 kcloud exporter(2026-10) 라벨로 가속기 목록이 조립되는지 점검.

샘플은 master3 Prometheus 실측(cluster="furiosa", 워커 1대, NPU 1장) 기준.
"""
import asyncio

from app.api.v2 import accelerators as acc

NODE = "k8s-furiosa-rngd-bg4wv22f4gpg-default-worker-mlkzg-72ljx"
BASE = {"cluster": "furiosa", "device": "npu0", "instance": NODE, "node": NODE, "pci": "0000:00:05.0"}


def _fake_prometheus(query):
    def s(value, **extra):
        return [{"metric": {**BASE, **extra}, "value": [0, value]}]

    if query.startswith("avg(kcloud_furiosa_core_utilization"):
        return s("12.5")  # 코어 8개를 PromQL이 카드 단위로 평균낸 결과
    if "kcloud_furiosa_temperature_celsius" in query:
        assert 'sensor="soc_peak"' in query, query
        return s("34.641", sensor="soc_peak")
    if "kcloud_furiosa_power_watts" in query:
        return s("37")
    if "kcloud_furiosa_memory_used_bytes" in query:
        return s("0")
    if "kcloud_furiosa_memory_total_bytes" in query:
        return s("51002736640")
    if "kcloud_furiosa_device_alive" in query:
        return s("1")
    raise AssertionError(f"예상하지 못한 쿼리: {query}")


def test_furiosa_accelerator_from_kcloud_labels():
    calls = []

    async def fake_instant(query):
        calls.append(query)
        return _fake_prometheus(query)

    orig = acc.prometheus_client.instant
    acc.prometheus_client.instant = fake_instant
    try:
        acc_map, warnings = asyncio.run(acc._collect_accelerators("furiosa", "furiosa", node=NODE))
    finally:
        acc.prometheus_client.instant = orig

    assert warnings == [], warnings
    assert list(acc_map) == ["npu0"]  # uuid가 없으므로 device가 카드 ID
    item = acc._to_item("npu0", acc_map["npu0"], "furiosa", "furiosa", None)
    assert item.node == NODE
    assert item.model == "RNGD"
    assert item.utilization_percent == 12.5
    assert item.temperature_celsius == 34.641
    assert item.power_watts == 37.0
    assert item.memory_total_bytes == 51002736640
    assert item.healthy is True
    assert all("furiosa_npu" not in q for q in calls), calls


def test_furiosa_single_metric_filters_by_device():
    calls = []

    async def fake_instant(query):
        calls.append(query)
        return _fake_prometheus(query)

    orig = acc.prometheus_client.instant
    acc.prometheus_client.instant = fake_instant
    try:
        value, warnings = asyncio.run(acc._single_metric("furiosa", "furiosa", NODE, "npu0", "power"))
    finally:
        acc.prometheus_client.instant = orig

    assert value == 37.0 and warnings == []
    assert 'device="npu0"' in calls[0], calls[0]
