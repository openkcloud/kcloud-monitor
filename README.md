# KCloud Monitor

> AI 반도체 통합 모니터링 플랫폼 (v2)

KCloud Monitor는 AI 반도체(GPU, NPU)와 클라우드 인프라(K8s·OpenStack)의 자원·전력을 통합 모니터링하는 FastAPI 기반 REST API 서비스입니다.

![GitHub license](https://img.shields.io/badge/license-Apache%202.0-blue.svg)
![Python version](https://img.shields.io/badge/python-3.12-green.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.119%2B-teal.svg)
![API Version](https://img.shields.io/badge/API-v2%20partial-yellow)

[![Korean](https://img.shields.io/badge/lang-한국어-red)](README.md)
[![English](https://img.shields.io/badge/lang-English-blue)](README_EN.md)

## 현재 상태: v2 부분 구현

v1 API(프로토타입)는 종료되었습니다(`docs/temp/02-decisions/design_contracts.md` §1).
**clusters · nodes · accelerators · monitoring · workloads** 도메인은 Prometheus 실데이터로 동작합니다(78/106).
나머지 **storage · openstack · resource-map · export**는 아직 스텁(`status: not_implemented`)을 반환합니다.

- v1 구현(GPU/DCGM·Kepler·IPMI 수집 로직, 익스포터 등)은 git 이력에서 참조 가능하며 실제 구현 시 재사용합니다.
- 경로 SoT: `docs/temp/04-reference/sample_api.md` (Monitor 81 + Resource-Map 8) + `docs/temp/01-domain-plans/openkcloud_storage_ceph_plan.md` (S1~S10) = **canonical 99개** (+별칭 4, 인증 3).

## API 구조 (`/api/v2`)

| 영역 | 경로 | 내용 | 수량 |
|------|------|------|------|
| Clusters | `/clusters/{c}` | 클러스터 요약·토폴로지·전력 [P1] | 5 |
| Nodes & Hardware | `/clusters/{c}/nodes/{n}/*` | 노드 메트릭·전력 [P2] + IPMI 실측 [P3] | 13 |
| Accelerators & Partitions | `.../accelerators/{id}/*` | GPU/NPU 통합, 파티션(MIG/vGPU/slice) [P4·P5] | 12(+별칭 2) |
| Storage (Ceph) | `/clusters/{c}/storage/*` | Rook-Ceph S1~S10 (v2 신규 도메인) | 10 |
| OpenStack | `/clusters/{c}/openstack/*` | 프로젝트·하이퍼바이저·VM·전력 귀속 [P6] | 13 |
| Workloads | `/clusters/{c}/workloads/*` | Pod/Container/Namespace [P7] | 11(+별칭 2) |
| Workloads (전역) | `/workloads/*` | 포탈용 전역 진입점 (`_links.canonical`) | 11 |
| Monitoring | `/monitoring/*` | 횡단 전력 집계 [P8]·시계열·SSE 스트림 | 10 |
| Export | `/export/*` | 전력/메트릭/리포트 내보내기 | 3 |
| Resource-Map | `/resource-map/*` | 자원 계보 원장(GPU→VM→Pod), Discovery | 8 |
| System | `/system/*` | 헬스·버전·자체 메트릭 (공개, 실동작) | 3 |
| Auth | `/auth/*` | JWT 발급 (Gateway 도입 전 개발용) | 3 |

## 빠른 시작

```bash
# 1. 의존성 설치
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 환경 변수
cp .env.example .env

# 3. 서버 실행
python run.py --port 8000
```

```bash
# 헬스체크 (공개)
curl http://localhost:8000/api/v2/system/health

# 로그인 → 토큰
curl -X POST http://localhost:8000/api/v2/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"changeme"}'

# 실데이터 조회 예시 (전력 3계층 요약)
curl -H "Authorization: Bearer <TOKEN>" \
  http://localhost:8000/api/v2/monitoring/power/summary
```

- Swagger UI: http://localhost:8000/docs
- 테스트: `pytest tests/ -v` (route inventory 106개·인증·응답 계약 검증)

## v2 목표 아키텍처 (구현 예정)

| 백엔드 | 용도 | 상태 |
|--------|------|------|
| **Mimir** (PromQL) | 중앙 메트릭 — 클러스터별 Alloy remote_write | 설정 placeholder |
| **PostgreSQL** | resource-map 원장(자원 계보) | 설정 placeholder |
| **Redis(+Streams)** | 캐시 + 서비스 간 이벤트 버스 | 설정 placeholder |
| **OpenStack API / libvirt** | VM 매핑·GPU passthrough·전력 귀속(A안) | 설정 placeholder |

전력 계층(P1~P8)·신뢰도 표기(`power_estimation`, `attribution.method`)는
`docs/temp/01-domain-plans/openkcloud_power_attribution_plan.md`를 따릅니다.

## 로드맵

- ✅ v2 라우팅/인증/경로 구조 확정
- ✅ clusters·nodes·accelerators·monitoring·workloads 실구현 (Prometheus 데이터소스)
- 🚧 storage(Ceph)·openstack·resource-map·export 도메인 실구현
- 📋 resource-map Discovery 수집기, 전력 귀속 recording rules

## 문서

| 문서 | 설명 |
|------|------|
| [빠른 시작](docs/QUICK_START.md) | 설치·실행·로그인·API 호출·테스트 |
| [API 가이드](docs/API_GUIDE.md) | 전체 106 라우트 표·인증·공통 파라미터·응답 정책·SSE |
| [아키텍처 개요](docs/ARCHITECTURE_OVERVIEW.md) | 6계층 자원 모델·데이터소스·전력 계층 P1~P8·resource-map |
| [NPU 수집 설정](docs/PROMETHEUS_NPU_SETUP.md) | Furiosa exporter + hwmon 수집 환경 |

> 상세 설계 SoT(설계서·의사결정 기록)는 내부 문서 `docs/temp/`(git 미추적)에서 관리하며, 확정 내용만 `docs/`로 반영합니다.

### 🔧 기술 스택

- **Framework**: FastAPI 0.119+, Python 3.12
- **Data Validation**: Pydantic 2.12+
- **Metrics**: prometheus-client (자체 메트릭 노출)
- **Server**: Uvicorn (ASGI)

## 기여하기

버그 리포트나 기능 제안은 이슈를 통해 제출해 주세요.

```bash
git clone https://github.com/openkcloud/kcloud-monitor.git
git checkout -b feature/your-feature
```

## 라이선스

이 프로젝트는 Apache License 2.0 하에 배포됩니다. 자세한 내용은 [LICENSE](LICENSE) 파일을 참조하세요.

```
Copyright 2025 OpenKCloud Community

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
```

## 문의

- **개발**: OpenKCloud 커뮤니티
- **이슈**: [GitHub Issues](https://github.com/openkcloud/kcloud-monitor/issues)

---

**KCloud Monitor v2** | AI 반도체 통합 모니터링 플랫폼
