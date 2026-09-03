# API 사용 가이드 (v2)

KCloud Monitor v2 API 가이드입니다. 현재는 **스캐폴드 단계**로, 라우팅·인증·경로 구조가 확정되어 있고 모든 엔드포인트가 스텁 응답을 반환합니다.

## 목차
- [기본 정보](#기본-정보)
- [인증](#인증)
- [공통 파라미터](#공통-파라미터)
- [공통 응답 정책](#공통-응답-정책)
- [스텁 응답 형태](#스텁-응답-형태)
- [엔드포인트 전체 목록](#엔드포인트-전체-목록)
- [SSE 스트리밍](#sse-스트리밍)

## 기본 정보

```
Base URL: http://localhost:8000/api/v2
Swagger:  http://localhost:8000/docs
```

- 경로 약어: `{c}` = cluster, `{n}` = node, `{id}` = accelerator ID, `{pid}` = partition ID, `{ns}` = namespace
- 네이밍: URL 경로 kebab-case(`gpu-passthrough`), 쿼리/JSON 필드 snake_case(`sort_by`, `power_watts`), 단위 접미사(`_watts`, `_percent`, `_celsius`)

## 인증

JWT Bearer 또는 `X-API-Key` 병행 인증입니다. System(`/system/*`)과 Auth(`/auth/*`)만 공개입니다.

```bash
# 1) 로그인 → JWT 발급
curl -X POST http://localhost:8000/api/v2/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"changeme"}'

# 2) Bearer 토큰으로 호출
curl -H "Authorization: Bearer <TOKEN>" http://localhost:8000/api/v2/clusters

# (대안) API Key — .env에 API_KEY 설정 시
curl -H "X-API-Key: <KEY>" http://localhost:8000/api/v2/clusters
```

| 엔드포인트 | 설명 |
|-----------|------|
| `POST /auth/login` | JSON 계정으로 JWT 발급 |
| `POST /auth/token` | HTTP Basic으로 JWT 발급 |
| `GET /auth/verify` | 토큰 유효성 확인 |

> 목표 구조는 API Gateway가 발급하는 JWT(RBAC/테넌트 클레임) + service-to-service JWT입니다. 현재 라우터는 Gateway 도입 전 개발용입니다.

## 공통 파라미터

**목록형 API** (`limit`/`offset` 페이징 + 라벨 필터):

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `limit` | `100` | 최대 반환 수 (max `1000`) |
| `offset` | `0` | 페이징 오프셋 |
| `sort_by` / `sort_order` | - / `asc` | 정렬 |
| `project` | - | OpenStack 프로젝트 필터 |
| `namespace` | - | K8s 네임스페이스 필터 |
| `service_name` | - | 논리 서비스 필터 |
| `workload_type` | - | `deployment`, `statefulset`, `job` 등 |
| `status` / `search` | - | 상태 필터 / 텍스트 검색 |
| `cluster` | - | 전역 목록(`/workloads/*`)에서만 — 클러스터 범위 경로에서는 경로 파라미터 |

**시계열 API**:

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `period` | `1h` | 조회 기간 (`start` 미지정 시) |
| `start` / `end` | - / now | ISO 8601 |
| `step` | `5m` | 데이터 포인트 간격 |
| `aggregation` | `avg` | `avg` / `min` / `max` / `sum` |

## 공통 응답 정책

모든 응답(REST, SSE data 이벤트)에 데이터 신선도 필드가 포함됩니다.

| 필드 | 설명 |
|------|------|
| `status` | `success` \| `partial` \| `error` (스텁 단계: `not_implemented`) |
| `observed_at` | 데이터 수집 시각 (ISO 8601) |
| `warnings[]` | `STALE_DATA`, `PARTIAL_SOURCE`, `ESTIMATED_POWER` 등 (정상 시 생략) |
| `partial_sources[]` | 일부 데이터소스 장애 시 실패 소스 목록 |

에러 스키마:

```json
{
  "status": "error",
  "error": {"code": "VALIDATION_ERROR", "message": "...", "retryable": false},
  "request_id": "req-...",
  "observed_at": "2026-08-06T00:00:00Z"
}
```

전력 응답에는 신뢰도 필드가 추가됩니다: `source`(kepler|dcgm|ipmi|derived), `power_estimation`(direct|attributed|proportional), `attribution.method`.

## 스텁 응답 형태

구현 전까지 모든 엔드포인트는 HTTP 200 + 아래 형태를 반환합니다. 포탈/클라이언트는 이 단계에서 경로·파라미터·인증 연동을 검증할 수 있습니다.

```json
{
  "status": "not_implemented",
  "api": "GET /api/v2/clusters/mgmt/nodes/w1/accelerators",
  "path_template": "/api/v2/clusters/{cluster}/nodes/{node}/accelerators",
  "description": "노드 가속기 목록(GPU/NPU 통합, UUID 식별)",
  "data_sources": ["Mimir(DCGM_FI_DEV_* + furiosa_npu_*)", "resource-map 원장"],
  "design_ref": "sample_api.md §4.1",
  "data": null,
  "observed_at": "2026-08-06T00:00:00Z",
  "warnings": ["NOT_IMPLEMENTED"],
  "params": {"limit": 100, "offset": 0, "sort_order": "asc"}
}
```

## 엔드포인트 전체 목록

canonical 99개 + 별칭 4개 + 인증 3개 = **106 라우트**. 전부 `GET`(예외: `POST /auth/*`, `POST /resource-map/discovery/trigger`).

### Clusters (5)

| 엔드포인트 | 설명 |
|-----------|------|
| `/clusters` | 클러스터 목록 (management/service 구분) |
| `/clusters/{c}` | 클러스터 상세 |
| `/clusters/{c}/summary` | 리소스 요약 KPI |
| `/clusters/{c}/topology` | 계층형 토폴로지 (Node→Pod→Container→가속기) |
| `/clusters/{c}/power` | 클러스터 전력 합계 [P1] |

### Nodes (10) + Hardware/IPMI (3)

| 엔드포인트 | 설명 |
|-----------|------|
| `/clusters/{c}/nodes` · `/nodes/summary` · `/nodes/{n}` | 목록 / 집계 / 상세 |
| `/clusters/{c}/nodes/{n}/metrics` | CPU/MEM/Disk/Net 종합 [M1] |
| `/clusters/{c}/nodes/{n}/power` · `/power/timeseries` | Kepler 전력 [P2] |
| `/clusters/{c}/nodes/{n}/cpu` · `/memory` · `/storage` · `/network` | 자원별 상세 |
| `/clusters/{c}/nodes/{n}/hardware/sensors` | IPMI 센서 전체 (물리 노드 전용) |
| `/clusters/{c}/nodes/{n}/hardware/power` | BMC 전력 실측 [P3] |
| `/clusters/{c}/nodes/{n}/hardware/temperature` | IPMI 온도 |

### Accelerators (8) + Partitions (4)

| 엔드포인트 | 설명 |
|-----------|------|
| `.../nodes/{n}/accelerators` · `/summary` · `/topology` | GPU/NPU 목록 / 집계 / NVLink·PCIe 토폴로지 |
| `.../accelerators/{id}` · `/metrics` · `/temperature` | 상세 / 실시간 메트릭 [M2] / 온도 |
| `.../accelerators/{id}/power` · `/power/timeseries` | DCGM 전력 실측 [P4] |
| `.../accelerators/{id}/partitions` · `/{pid}` | 파티션(MIG/vGPU/NPU slice) 목록 / 상세 |
| `.../partitions/{pid}/power` · `/power/timeseries` | 파티션 전력 추정 [P5] |

### Storage — Ceph (10, v2 신규 도메인)

| 엔드포인트 | 설명 |
|-----------|------|
| `/clusters/{c}/storage/ceph/summary` [S1] · `/health` [S2] | Ceph 요약 / health 상세 |
| `/clusters/{c}/storage/ceph/capacity` [S3] · `/capacity/timeseries` [S9] | 용량 / 시계열 |
| `/clusters/{c}/storage/ceph/osds` [S4] · `/osds/{osd_id}` [S5] | OSD 목록 / 상세 |
| `/clusters/{c}/storage/ceph/pools` [S6] · `/pools/{pool}` [S7] | 풀 목록 / 상세 |
| `/clusters/{c}/storage/ceph/pgs` [S8] | PG 상태 요약 |
| `/clusters/{c}/storage/summary` [S10] | 스토리지 통합 요약 |

### OpenStack (13, 관리 클러스터 전용)

| 엔드포인트 | 설명 |
|-----------|------|
| `/clusters/{c}/openstack/summary` | 전체 현황 |
| `/clusters/{c}/openstack/projects` · `/{id}` · `/{id}/summary` | 프로젝트(과금 단위) |
| `/clusters/{c}/openstack/hypervisors` · `/{host}` · `/{host}/vms` | 하이퍼바이저·VM 배치 |
| `/clusters/{c}/openstack/vms` · `/summary` · `/{vm_id}` · `/{vm_id}/metrics` | VM 목록/집계/상세/메트릭 |
| `/clusters/{c}/openstack/vms/{vm_id}/power` | VM 전력 귀속 [P6] |
| `/clusters/{c}/openstack/vms/{vm_id}/gpu-passthrough` | passthrough 장치 (libvirt hostdev 확정) |

### Workloads — 클러스터 범위 (11)

| 엔드포인트 | 설명 |
|-----------|------|
| `/clusters/{c}/workloads/pods` · `/summary` · `/{ns}/{pod}` | Pod 목록/집계/상세 |
| `/clusters/{c}/workloads/pods/{ns}/{pod}/power` | Pod 전력 귀속 [P7] |
| `/clusters/{c}/workloads/pods/{ns}/{pod}/containers` · `/containers/{name}/metrics` | 컨테이너 목록/메트릭 |
| `/clusters/{c}/workloads/pods/{ns}/{pod}/accelerators` | Pod 가속기 할당 |
| `/clusters/{c}/workloads/containers` · `/{container_id}` | 클러스터 전역 컨테이너 |
| `/clusters/{c}/namespaces` · `/{ns}/summary` | 네임스페이스 |

### Workloads — 전역 진입점 (11, 포탈용)

응답에 `_links.self` + `_links.canonical` 포함. 전역 목록 기본 범위는 서비스 클러스터.

| 엔드포인트 | 설명 |
|-----------|------|
| `/workloads/pods` · `/summary` | 전 클러스터 Pod 인덱스 |
| `/workloads/pods/{c}/{ns}/{pod}` (+ `/power`, `/containers`, `/accelerators`) | canonical 위임 |
| `/workloads/services` · `/summary` | 논리 서비스 목록 (`service_name` 파생 그룹) |
| `/workloads/services/{c}/{ns}/{name}` (+ `/pods`, `/power`) | 서비스 상세/소속 Pod/전력 |

### Monitoring — 횡단 집계 (10)

| 엔드포인트 | 설명 |
|-----------|------|
| `/monitoring/overview` | 전체 시스템 KPI |
| `/monitoring/power/summary` [P8] · `/breakdown` · `/timeseries` · `/efficiency` | 전력 요약/분해/시계열/효율 |
| `/monitoring/metrics/timeseries` · `/query` [M5] | 메트릭 질의 (허용 목록 기반) |
| `/monitoring/temperature/timeseries` | 온도 통합 시계열 |
| `/monitoring/stream/power` · `/stream/metrics` | **SSE** 실시간 스트림 |

### Export (3) / Resource-Map (8) / System (3)

| 엔드포인트 | 설명 |
|-----------|------|
| `/export/power` · `/metrics` (`format=csv\|excel\|parquet`) · `/report` (`report_type=daily\|weekly\|monthly`) | 데이터/리포트 내보내기 |
| `/resource-map/accelerators/{id}` (+ `/history`) | 가속기 계보 (GPU→VM→Pod) / 이력 |
| `/resource-map/partitions/{pid}` · `/containers/{pod_uid}/{container}` · `/vms/{vm_uuid}` · `/physical-servers/{server_id}` | 자원별 계보 |
| `/resource-map/relationships` | 관계 그래프 질의 |
| `POST /resource-map/discovery/trigger` | Discovery 수동 스캔 (202) |
| `/system/health` · `/version` · `/metrics` | 헬스 / 버전 / 자체 Prometheus 메트릭 (**공개, 실동작**) |

### 별칭 — 단축 경로 (4)

UUID 직접 조회용. 응답 `_links.canonical`로 정규 경로 안내.

| 별칭 | canonical |
|------|-----------|
| `/clusters/{c}/accelerators/{id}` | `/clusters/{c}/nodes/{n}/accelerators/{id}` |
| `/clusters/{c}/accelerators/{id}/partitions/{pid}` | `.../accelerators/{id}/partitions/{pid}` |
| `/clusters/{c}/pods/{ns}/{pod}` | `/clusters/{c}/workloads/pods/{ns}/{pod}` |
| `/clusters/{c}/containers/{id}` | `/clusters/{c}/workloads/containers/{id}` |

## SSE 스트리밍

v1 WebSocket은 폐기되고 SSE로 통일되었습니다.

- `Content-Type: text/event-stream`, 15초마다 `heartbeat` 이벤트(`observed_at`만 포함)
- data 이벤트는 REST와 동일 응답 모델 공유 (클라이언트가 같은 타입으로 역직렬화)
- `Last-Event-ID` 헤더로 재개 지원 (구현 예정)

```bash
curl -N -H "Authorization: Bearer <TOKEN>" \
  http://localhost:8000/api/v2/monitoring/stream/power
```

스텁 단계에서는 heartbeat 1회 + 스텁 이벤트 1회 송신 후 종료합니다.
