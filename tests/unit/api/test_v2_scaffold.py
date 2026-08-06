"""
v2 스캐폴드 검증 — route inventory·인증·스텁 응답 계약.

설계 수량(SoT): sample_api.md Monitor 81 + Resource-Map 8 + storage_ceph_plan S1~S10
= canonical 99개. 별칭(단축 경로) 4개와 auth 3개는 카탈로그 카운트 미포함.
"""
import re

from app.main import app

V2 = "/api/v2"

ALIAS_PATHS = {
    f"{V2}/clusters/{{cluster}}/accelerators/{{acc_id}}",
    f"{V2}/clusters/{{cluster}}/accelerators/{{acc_id}}/partitions/{{partition_id}}",
    f"{V2}/clusters/{{cluster}}/pods/{{namespace}}/{{pod}}",
    f"{V2}/clusters/{{cluster}}/containers/{{container_id}}",
}
AUTH_PATHS = {f"{V2}/auth/login", f"{V2}/auth/token", f"{V2}/auth/verify"}

# 인증 없이 접근 가능한 공개 경로 (System + Auth)
PUBLIC_PREFIXES = (f"{V2}/system/", f"{V2}/auth/")


def _v2_routes():
    """(method, path) 목록 — /api/v2 하위 API 라우트만."""
    routes = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None)
        if not path.startswith(V2) or not methods:
            continue
        for method in methods - {"HEAD", "OPTIONS"}:
            routes.append((method, path))
    return routes


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "test", path)


def test_route_inventory_counts():
    """카탈로그 canonical 99 + 별칭 4 + auth 3 = 총 106 라우트."""
    routes = _v2_routes()
    paths = {p for _, p in routes}
    alias = paths & ALIAS_PATHS
    auth = paths & AUTH_PATHS
    canonical = [(m, p) for m, p in routes if p not in ALIAS_PATHS and p not in AUTH_PATHS]

    assert len(alias) == 4, f"별칭 경로 누락: {sorted(ALIAS_PATHS - alias)}"
    assert len(auth) == 3
    assert len(canonical) == 99, (
        f"canonical 라우트 수 {len(canonical)} != 99 (sample_api 81 + resource-map 8 + storage 10)"
    )
    assert len(routes) == 106


def test_protected_routes_require_auth(client):
    """보호 경로는 인증 없이 401."""
    for method, path in _v2_routes():
        if path.startswith(PUBLIC_PREFIXES):
            continue
        response = client.request(method, _fill(path))
        assert response.status_code == 401, f"{method} {path} → {response.status_code} (401 기대)"


def test_public_endpoints_do_not_require_auth(client):
    assert client.get(f"{V2}/system/health").status_code == 200
    assert client.get(f"{V2}/system/version").status_code == 200
    assert client.get(f"{V2}/system/metrics").status_code == 200


def test_login_issues_token(client):
    response = client.post(
        f"{V2}/auth/login", json={"username": "testuser", "password": "testpass"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"


def test_all_get_routes_respond_with_stub_envelope(client, auth_headers):
    """모든 GET 라우트가 200을 반환하고, JSON 스텁은 공통 envelope(status/observed_at)를 갖는다."""
    for method, path in _v2_routes():
        if method != "GET":
            continue
        response = client.get(_fill(path), headers=auth_headers)
        assert response.status_code == 200, f"GET {path} → {response.status_code}"
        if path in AUTH_PATHS:
            continue  # 인증 유틸은 데이터 envelope 계약 대상 아님
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            continue  # /system/metrics(text), SSE(event-stream)는 본문 계약 제외
        body = response.json()
        assert "status" in body, f"GET {path}: status 필드 누락"
        assert "observed_at" in body, f"GET {path}: observed_at 필드 누락"


def test_discovery_trigger_returns_202(client, auth_headers):
    response = client.post(f"{V2}/resource-map/discovery/trigger", headers=auth_headers)
    assert response.status_code == 202


def test_global_workload_entry_includes_canonical_links(client, auth_headers):
    """전역 진입점/별칭은 _links.self + _links.canonical 필수 (design_contracts §6)."""
    response = client.get(f"{V2}/workloads/pods/c1/ns1/pod1", headers=auth_headers)
    links = response.json().get("_links", {})
    assert links.get("self") and links.get("canonical")

    response = client.get(f"{V2}/clusters/c1/pods/ns1/pod1", headers=auth_headers)
    links = response.json().get("_links", {})
    assert links.get("canonical", "").startswith("/api/v2/clusters/c1/workloads/pods/")


def test_sse_stream_stub_emits_heartbeat(client, auth_headers):
    response = client.get(f"{V2}/monitoring/stream/power", headers=auth_headers)
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    assert "event: heartbeat" in response.text
