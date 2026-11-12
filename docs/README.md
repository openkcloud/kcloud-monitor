# KCloud Monitor 문서

KCloud Monitor v2 (AI 반도체 통합 모니터링 API) 공식 문서입니다.

> 현재 코드는 **v2 스캐폴드** 단계입니다 — 라우팅·인증·경로 구조 확정, 전 엔드포인트 스텁 응답.

## 📚 문서 목록

### [빠른 시작 (QUICK_START.md)](./QUICK_START.md)
설치·실행, 로그인, 스텁 엔드포인트 호출, 테스트, Docker. **처음이라면 여기서 시작하세요.**

### [API 사용 가이드 (API_GUIDE.md)](./API_GUIDE.md)
`/api/v2` 전체 106 라우트 표, 인증, 공통 파라미터, 공통 응답 정책(envelope·에러 스키마), 스텁 응답 형태, SSE 계약.

### [아키텍처 개요 (ARCHITECTURE_OVERVIEW.md)](./ARCHITECTURE_OVERVIEW.md)
6계층 자원 모델(물리→관리K8s→OpenStack→서비스K8s→Pod), 데이터소스(Mimir·PostgreSQL·Redis·OpenStack), URL 설계 원칙, 전력 계층 P1~P8과 신뢰도 표기, resource-map(자원 계보)과 SoT 우선순위, NFR.

### [Prometheus NPU 수집 설정 (PROMETHEUS_NPU_SETUP.md)](./PROMETHEUS_NPU_SETUP.md)
Furiosa Metrics Exporter(1차) + node_exporter hwmon(보조) 수집 환경 구성. v2 구현 단계에서도 동일 계약.

## 문서 체계

- `docs/` (이 폴더): 저장소 공개 문서 — 사용·운영 관점
- `docs/temp/` (git 미추적): 내부 설계 SoT — 상세 설계서·의사결정 기록·검증 리포트.
  공개 문서와 설계가 충돌하면 내부 SoT가 우선하며, 확정된 내용만 이 폴더로 반영합니다.

## 외부 문서

- Swagger UI: http://localhost:8000/docs (서버 실행 시)
- ReDoc: http://localhost:8000/redoc
