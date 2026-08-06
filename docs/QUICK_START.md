# Quick Start Guide (v2)

KCloud Monitor v2의 빠른 시작 가이드입니다.

> **현재 단계: 스캐폴드** — 라우팅·인증·경로 구조만 확정되어 있고, 모든 엔드포인트는
> 정의·데이터소스·설계 참조를 담은 스텁 응답(`status: not_implemented`)을 반환합니다.
> 포탈/클라이언트의 경로·인증 연동 검증 용도로 사용하세요.

## 설치 및 실행

```bash
# 1. 가상환경 및 의존성
python3.12 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. 환경 변수
cp .env.example .env
# 스캐폴드 단계에서는 인증 계정(API_AUTH_*)만 조정하면 됨.
# MIMIR_URL/DATABASE_URL/REDIS_URL/OPENSTACK_* 는 구현 시 활성화되는 placeholder.

# 3. 서버 실행
python run.py --port 8000
# 또는: uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

## 기본 사용법

### 1) 헬스체크 (공개)

```bash
curl http://localhost:8000/api/v2/system/health
```

```json
{"status": "healthy", "phase": "v2-scaffold", "backends": {"mimir": "not_configured", ...}}
```

### 2) 로그인 → JWT

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v2/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"changeme"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
```

### 3) 스텁 엔드포인트 호출

```bash
# 클러스터 목록
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v2/clusters

# 노드 가속기 목록 (계층형 canonical 경로)
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v2/clusters/mgmt/nodes/w1/accelerators?limit=10"

# 전력 요약 [P8]
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v2/monitoring/power/summary
```

스텁 응답에는 해당 API의 정의(`description`), 구현 시 사용할 데이터소스(`data_sources`),
설계 참조(`design_ref`)가 포함되어 있어 Swagger와 함께 API 계약을 미리 확인할 수 있습니다.

### 4) SSE 스트림 (스텁: heartbeat 1회 + 스텁 이벤트 후 종료)

```bash
curl -N -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v2/monitoring/stream/power
```

## API 문서

- Swagger UI: http://localhost:8000/docs — 영역별 태그, 한국어 설명 포함
- ReDoc: http://localhost:8000/redoc
- 전체 라우트 표: [API_GUIDE.md](./API_GUIDE.md)

## 테스트

```bash
pytest tests/ -v
```

route inventory(106개 고정), 인증 강제(무인증 401), 스텁 envelope, `_links.canonical`,
SSE heartbeat를 검증합니다. 라우트를 추가/삭제하면 inventory 테스트가 실패하므로
설계 카탈로그와 함께 갱신해야 합니다.

## Docker

```bash
cp .env.example .env
docker-compose up -d
curl http://localhost:8000/api/v2/system/health
```

## 문제 해결

| 증상 | 원인/해결 |
|------|----------|
| 401 Unauthorized | 토큰 누락/만료 — `/auth/login` 재발급, 또는 `X-API-Key` 헤더(.env `API_KEY` 설정 시) |
| 모든 응답이 `not_implemented` | 정상입니다 — 스캐폴드 단계의 계약된 동작 |
| `/system/health`의 backends가 전부 `not_configured` | 정상입니다 — 데이터소스 클라이언트는 구현 단계에서 연결 |
