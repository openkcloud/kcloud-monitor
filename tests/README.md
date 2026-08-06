# KCloud Monitor - Test Suite (v2 스캐폴드)

## 📁 구조

```
tests/
├── conftest.py                        # 공통 픽스처 (settings 오버라이드, client, JWT)
└── unit/
    └── api/
        └── test_v2_scaffold.py        # route inventory·인증·스텁 응답 계약 검증
```

## 🚀 실행

```bash
pytest tests/ -v
```

## ✅ 검증 범위

| 테스트 | 내용 |
|--------|------|
| `test_route_inventory_counts` | canonical 99(sample_api 81 + resource-map 8 + storage 10) + 별칭 4 + auth 3 = 106 라우트 |
| `test_protected_routes_require_auth` | 보호 경로 전체 무인증 401 |
| `test_public_endpoints_do_not_require_auth` | system health/version/metrics 공개 |
| `test_login_issues_token` | JWT 발급 |
| `test_all_get_routes_respond_with_stub_envelope` | 전체 GET 200 + 공통 envelope(`status`, `observed_at`) |
| `test_discovery_trigger_returns_202` | POST discovery 비동기 202 |
| `test_global_workload_entry_includes_canonical_links` | 전역 진입점/별칭 `_links.canonical` (design_contracts §6) |
| `test_sse_stream_stub_emits_heartbeat` | SSE 스텁 heartbeat 이벤트 |

## 🔧 픽스처 (conftest.py)

- `test_settings` — 테스트용 설정 오버라이드
- `client` — FastAPI TestClient
- `auth_token` / `auth_headers` — 유효한 JWT

## 📝 실제 구현 시

엔드포인트를 스텁에서 실제 구현으로 교체할 때, 해당 도메인의 유닛 테스트 파일을
`tests/unit/api/`에 추가하고 crud/서비스 계층을 모킹한다. v1 테스트(191개)는
git 이력(`feat/v2-scaffold` 이전)에서 참고 가능.
