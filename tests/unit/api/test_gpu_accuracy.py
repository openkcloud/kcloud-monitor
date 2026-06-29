"""
GPU collection accuracy regression tests (PR A: B1/B2/B3/B5/G2/G3).

Covers the data-layer fixes that have no live-Prometheus dependency by mocking
app.crud.prometheus_client, plus the /accelerators/gpus/inventory endpoint.
"""
import asyncio
from unittest.mock import patch

import pytest

from app import crud


def _run(coro):
    return asyncio.run(coro)


def _series(labels, value):
    return {"metric": labels, "value": [0, str(value)]}


class _FakeProm:
    """Stub prometheus_client.query that dispatches by metric name prefix."""

    def __init__(self, by_metric):
        self.by_metric = by_metric

    def query(self, q):
        for name, result in self.by_metric.items():
            if q.startswith(name):
                return {"data": {"result": result}}
        return {"data": {"result": []}}


class TestEnumerationAndMemory:
    """B1 (enumerate via TEMP/POWER, MIG parents) + B2 (memory = FB_USED+FB_FREE)."""

    def test_mig_parent_enumerated_without_gpu_util(self):
        # worker-2 MIG-enabled GPU exposes TEMP/POWER but no GPU_UTIL (B1).
        prom = _FakeProm({
            "DCGM_FI_DEV_GPU_TEMP": [
                _series({"Hostname": "worker-2", "device": "nvidia0", "gpu": "0",
                         "UUID": "GPU-404f", "modelName": "NVIDIA A30"}, 45)
            ],
            "DCGM_FI_DEV_POWER_USAGE": [
                _series({"Hostname": "worker-2", "device": "nvidia0", "gpu": "0",
                         "UUID": "GPU-404f", "modelName": "NVIDIA A30"}, 70)
            ],
            "DCGM_FI_DEV_FB_USED": [_series({"device": "nvidia0"}, 1024)],
            "DCGM_FI_DEV_FB_FREE": [_series({"device": "nvidia0"}, 23552)],
            # FB_TOTAL intentionally absent on this cluster (B2).
            "DCGM_FI_DEV_FB_TOTAL": [],
            "DCGM_FI_DEV_CUDA_COMPUTE_CAPABILITY": [],
        })
        with patch.object(crud, "prometheus_client", prom):
            gpus = _run(crud.get_dcgm_gpu_info(None))
        assert len(gpus) == 1
        assert gpus[0]["uuid"] == "GPU-404f"
        # memory_total_mb derived as used + free (B2)
        assert gpus[0]["memory_total_mb"] == 1024 + 23552

    def test_uuid_dedup_across_temp_and_power(self):
        labels = {"Hostname": "worker-3", "device": "nvidia0", "gpu": "0",
                  "UUID": "GPU-8a67", "modelName": "NVIDIA A30"}
        prom = _FakeProm({
            "DCGM_FI_DEV_GPU_TEMP": [_series(labels, 40)],
            "DCGM_FI_DEV_POWER_USAGE": [_series(labels, 60)],
            "DCGM_FI_DEV_FB_USED": [_series({"device": "nvidia0"}, 0)],
            "DCGM_FI_DEV_FB_FREE": [_series({"device": "nvidia0"}, 24576)],
            "DCGM_FI_DEV_FB_TOTAL": [],
            "DCGM_FI_DEV_CUDA_COMPUTE_CAPABILITY": [],
        })
        with patch.object(crud, "prometheus_client", prom):
            gpus = _run(crud.get_dcgm_gpu_info(None))
        # Same UUID in both TEMP and POWER → one entry, not two (UUID dedup).
        assert len(gpus) == 1


class TestPassthroughClassification:
    """B5/G3: passthrough/VM GPUs are kept and classified."""

    def test_passthrough_gpu_flagged(self):
        labels = {"Hostname": "vm-1", "device": "nvidia0", "gpu": "0",
                  "UUID": "GPU-vm01", "modelName": "NVIDIA A30",
                  "hypervisor_host": "worker-1", "vm_type": "kvm",
                  "gpu_allocation": "passthrough"}
        prom = _FakeProm({
            "DCGM_FI_DEV_GPU_TEMP": [_series(labels, 50)],
            "DCGM_FI_DEV_POWER_USAGE": [_series(labels, 80)],
            "DCGM_FI_DEV_FB_USED": [_series({"device": "nvidia0"}, 0)],
            "DCGM_FI_DEV_FB_FREE": [_series({"device": "nvidia0"}, 24576)],
            "DCGM_FI_DEV_FB_TOTAL": [],
            "DCGM_FI_DEV_CUDA_COMPUTE_CAPABILITY": [],
        })
        with patch.object(crud, "prometheus_client", prom):
            gpus = _run(crud.get_dcgm_gpu_info(None))
        assert len(gpus) == 1
        assert gpus[0]["is_vm_gpu"] is True
        assert gpus[0]["gpu_allocation"] == "passthrough"


class TestMigInstanceUtilization:
    """G2: MIG instance utilization from GR_ENGINE_ACTIVE, GPU_I_ID required."""

    def test_only_mig_series_kept(self):
        rows = [
            _series({"Hostname": "worker-2", "device": "nvidia0", "gpu": "0",
                     "GPU_I_ID": "3", "GPU_I_PROFILE": "1g.6gb", "UUID": "GPU-404f"}, 0.42),
            # non-MIG GPU reports GR_ENGINE_ACTIVE without GPU_I_ID → skipped.
            _series({"Hostname": "worker-3", "device": "nvidia0", "gpu": "0",
                     "UUID": "GPU-8a67"}, 0.9),
        ]
        out = crud._parse_mig_instance_utilization(rows)
        assert len(out) == 1
        assert out[0]["parent_uuid"] == "GPU-404f"
        assert out[0]["gpu_instance_id"] == 3
        assert out[0]["utilization_percent"] == pytest.approx(42.0)


class TestInventoryGrouping:
    """G3: _build_gpu_inventory parent+child grouping and counts."""

    def test_counts_and_children(self):
        gpus = [
            {"gpu_id": "nvidia0", "uuid": "GPU-404f", "hostname": "worker-2",
             "model_name": "A30", "memory_total_mb": 24576,
             "gpu_allocation": "kubernetes", "is_vm_gpu": False},
            {"gpu_id": "nvidia0", "uuid": "GPU-vm01", "hostname": "vm-1",
             "model_name": "A30", "memory_total_mb": 24576,
             "gpu_allocation": "passthrough", "is_vm_gpu": True},
        ]
        migs = [
            {"parent_uuid": "GPU-404f", "gpu_instance_id": 3, "profile": "1g.6gb",
             "utilization_percent": 42.0},
            {"parent_uuid": "GPU-404f", "gpu_instance_id": 5, "profile": "1g.6gb",
             "utilization_percent": 0.0},
        ]
        inv = crud._build_gpu_inventory(gpus, migs, {"worker-2": 4, "vm-1": 1})
        s = inv["summary"]
        assert s["physical_gpus"] == 2
        assert s["mig_instances"] == 2
        assert s["total_devices"] == 4
        assert s["passthrough_gpus"] == 1
        assert s["kubernetes_gpus"] == 1
        assert s["k8s_advertised_gpus"] == 5
        w2 = next(n for n in inv["nodes"] if n["hostname"] == "worker-2")
        assert w2["mig_instances"] == 2
        assert w2["gpus"][0]["mig_enabled"] is True
        assert len(w2["gpus"][0]["children"]) == 2


class TestInventoryEndpoint:
    """G3: /accelerators/gpus/inventory endpoint wiring."""

    def test_inventory_endpoint(self, client, auth_headers):
        fake = {
            "summary": {"physical_gpus": 1, "mig_instances": 4, "total_devices": 5,
                        "passthrough_gpus": 0, "kubernetes_gpus": 1,
                        "k8s_advertised_gpus": 4},
            "nodes": [{"hostname": "worker-2", "physical_gpus": 1, "mig_instances": 4,
                       "k8s_advertised_gpus": 4, "gpus": []}],
        }

        async def _fake_inventory(node=None):
            return fake

        with patch("app.crud.get_gpu_inventory", _fake_inventory):
            r = client.get("/api/v1/accelerators/gpus/inventory", headers=auth_headers)
        assert r.status_code == 200
        d = r.json()
        assert d["summary"]["total_devices"] == 5
        assert d["nodes"][0]["hostname"] == "worker-2"

    def test_inventory_not_shadowed_by_gpu_id_route(self, client, auth_headers):
        # /inventory must resolve to the inventory route, not /{gpu_id}.
        async def _fake_inventory(node=None):
            return {"summary": {}, "nodes": []}

        with patch("app.crud.get_gpu_inventory", _fake_inventory):
            r = client.get("/api/v1/accelerators/gpus/inventory", headers=auth_headers)
        assert r.status_code == 200
        assert "summary" in r.json()
