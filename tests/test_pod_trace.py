"""Pod 추적 자체 점검: 관리 클러스터 경로(VM 건너뜀), VM 경로(uuid 조인), 이름 중복, 없는 Pod."""
import asyncio

import pytest
from fastapi import HTTPException

from app.api.v2 import resource_map
from app.services.cluster_discovery import ClusterInfo


def _r(metric, value="1"):
    return {"metric": metric, "value": [0, value]}


NOVA = [
    _r({"name": "k8s-furiosa-worker-0", "uuid": "B881A36A-0000", "hypervisor_hostname": "compute1",
        "status": "ACTIVE", "tenant_id": "proj1"}),
    _r({"name": "registry", "uuid": "aff2b158", "hypervisor_hostname": "compute5", "status": "ACTIVE"}),
]


def _run(answers: dict, vendor=None, **kw):
    """answers: 쿼리 앞부분(메트릭 이름) → 결과. 매칭이 없으면 빈 결과."""
    async def fake_instant(query):
        for prefix, res in answers.items():
            if query.startswith(prefix):
                return res
        return []

    async def fake_get_cluster(name):
        return ClusterInfo(name=name, label_value=name, vendor=vendor)

    orig = (resource_map.prometheus_client.instant, resource_map.cluster_discovery.get_cluster)
    resource_map.prometheus_client.instant = fake_instant
    resource_map.cluster_discovery.get_cluster = fake_get_cluster
    try:
        return asyncio.run(resource_map.trace_pod(**kw))
    finally:
        resource_map.prometheus_client.instant, resource_map.cluster_discovery.get_cluster = orig


def _layers(resp):
    return {h.layer: h for h in resp.path}


def test_mgmt_pod_skips_vm_and_reaches_physical_server():
    resp = _run({
        "kube_pod_info": [_r({"namespace": "openstack", "pod": "keystone-api-x", "node": "controller", "uid": "u1",
                              "created_by_kind": "ReplicaSet", "created_by_name": "keystone-api-75dc"})],
        "kube_pod_status_phase": [_r({"phase": "Running"})],
        "kube_replicaset_owner": [_r({"owner_kind": "Deployment", "owner_name": "keystone-api"})],
        "kube_node_info": [_r({"node": "controller", "system_uuid": "4c4c4544-0031"})],
        "kube_node_status_condition": [_r({})],
        "openstack_nova_server_status": NOVA,
    }, pod="keystone-api-x")
    h = _layers(resp)
    assert resp.status == "success" and resp.cluster == "mgmt" and resp.namespace == "openstack"
    assert [x.layer for x in resp.path] == ["pod", "workload", "accelerator", "k8s_node", "vm", "physical_server"]
    assert h["workload"].name == "Deployment/keystone-api"
    assert h["accelerator"].status == "none"
    assert h["k8s_node"].state == "Ready"
    assert h["vm"].status == "skipped"
    assert h["physical_server"].name == "controller"
    assert h["physical_server"].href.endswith("/clusters/mgmt/nodes/controller")


def test_vm_node_matched_by_system_uuid_case_insensitive():
    resp = _run({
        "kube_pod_info": [_r({"cluster": "k8s-furiosa", "namespace": "default", "pod": "infer-1",
                              "node": "renamed-node", "created_by_kind": "DaemonSet", "created_by_name": "ds"})],
        "kube_node_info": [_r({"system_uuid": "b881a36a-0000"})],
        "openstack_nova_server_status": NOVA,
    }, vendor="furiosa", pod="infer-1")
    h = _layers(resp)
    assert h["workload"].name == "DaemonSet/ds"
    assert h["accelerator"].status == "unavailable"
    assert h["vm"].id == "B881A36A-0000" and h["vm"].project == "proj1" and "system_uuid" in h["vm"].via
    assert h["physical_server"].name == "compute1"
    assert h["physical_server"].href.endswith("/openstack/hypervisors/compute1")
    assert resp.status == "partial" and resp.warnings == ["ACCELERATOR_NOT_AVAILABLE"]


def test_service_node_without_vm_match_is_unavailable_not_skipped():
    resp = _run({
        "kube_pod_info": [_r({"cluster": "k8s-x", "namespace": "default", "pod": "p", "node": "ghost"})],
        "openstack_nova_server_status": NOVA,
    }, pod="p")
    h = _layers(resp)
    assert h["workload"].status == "none"
    assert h["vm"].status == "unavailable" and h["physical_server"].status == "unavailable"


def test_same_name_in_two_places_returns_candidates():
    resp = _run({"kube_pod_info": [
        _r({"namespace": "openstack", "pod": "mysql-0"}),
        _r({"cluster": "k8s-furiosa", "namespace": "default", "pod": "mysql-0"}),
    ]}, pod="mysql-0")
    assert resp.status == "ambiguous" and resp.path == []
    assert [(c.cluster, c.namespace) for c in resp.candidates] == [("k8s-furiosa", "default"), ("mgmt", "openstack")]


def test_unknown_pod_is_404_but_prometheus_down_is_503():
    with pytest.raises(HTTPException) as e:
        _run({"vector(1)": [_r({})]}, pod="nope")
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        _run({}, pod="nope")
    assert e.value.status_code == 503


def test_node_without_node_info_is_unavailable():
    resp = _run({
        "kube_pod_info": [_r({"cluster": "k8s-x", "namespace": "default", "pod": "p", "node": "n1"})],
    }, pod="p")
    assert _layers(resp)["k8s_node"].status == "unavailable"
    assert "K8S_NODE_NOT_AVAILABLE" in resp.warnings
