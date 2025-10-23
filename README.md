# AI Accelerator & Infrastructure Monitoring API

## 개요

AI 가속기(GPU, NPU)와 인프라 전체를 모니터링하는 통합 FastAPI 기반 웹 서비스입니다. Prometheus를 통해 DCGM, Kepler, IPMI 등 다양한 메트릭 소스를 수집하고 REST API로 제공합니다.

**API Version**: 2.0.0
**Architecture**: 7-Domain Design (Accelerators, Infrastructure, Hardware, Clusters, Monitoring, Export, System)

## 주요 기능

### ✅ 구현 완료된 기능들

#### 1. Accelerators (AI 가속기 모니터링)
- **GPU 모니터링**: DCGM 기반 NVIDIA GPU 상세 모니터링
  - GPU 정보 조회 (모델, UUID, 드라이버 버전)
  - 실시간 성능 메트릭 (사용률, 전력, 온도, 메모리, 클럭, 에러)
  - 개별 GPU 전력 및 온도 모니터링
  - GPU 전체 요약 통계
- **NPU 모니터링**: Furiosa/Rebellions NPU 지원 (Placeholder, Exporter 설정 필요)
  - NPU 정보 및 메트릭 (사용률, 전력, 온도, 메모리, throughput, latency)
  - Furiosa 코어별 상태 모니터링
  - NPU 요약 통계
- **통합 가속기 뷰**: GPU + NPU 통합 조회 및 요약

#### 2. Infrastructure (인프라 모니터링)
- **Nodes**: Kepler 기반 노드 레벨 전력 분해 (CPU, DRAM, GPU)
- **Pods**: Kubernetes Pod별 전력 및 리소스 모니터링
- **Containers**: 컨테이너 메트릭 조회
- **VMs**: OpenStack VM 모니터링 (Placeholder, Collector 구현 필요)

#### 3. Hardware (물리 하드웨어 모니터링)
- **IPMI 센서**: 전력, 온도, 팬, 전압 센서 데이터 수집 (Placeholder, Exporter 설정 필요)
- **센서 상태 감지**: Normal/Warning/Critical 임계값 기반 알림
- **하드웨어 건강 요약**: 노드별 하드웨어 상태 통합

#### 4. Clusters (멀티 클러스터 관리)
- **클러스터 정보**: 여러 클러스터 통합 조회 및 관리
- **클러스터별 리소스**: 가속기, 노드, Pod, 전력 데이터
- **헬스체크**: 개별 클러스터 연결 상태 및 건강 상태

#### 5. Monitoring (크로스 도메인 통합 모니터링)
- **통합 전력 모니터링**: 가속기 + 인프라 + 하드웨어 전력 통합 집계
- **전력 분해 분석**: 클러스터/노드/네임스페이스/벤더/리소스타입별 분류
- **전력 효율성**: PUE(Power Usage Effectiveness) 계산 및 효율성 지표
- **시계열 데이터**: 전력/메트릭/온도 시계열 조회
- **실시간 스트리밍**: WebSocket 및 SSE를 통한 실시간 데이터 푸시

#### 6. Export (데이터 내보내기)
- **다양한 포맷**: JSON, CSV, Parquet, Excel, PDF
- **전력 데이터 내보내기**: 시계열 및 분해 데이터 내보내기
- **메트릭 내보내기**: 성능 메트릭 시계열 내보내기
- **종합 리포트**: Daily/Weekly/Monthly 템플릿 기반 리포트 생성

#### 7. System (시스템 정보 및 메트릭)
- **헬스체크**: API, Prometheus, Cache, Stream 상태
- **API 메트릭**: Prometheus 형식 메트릭 노출 (요청 수, 응답 시간, 에러율, 캐시 히트율)
- **버전 정보**: API 버전, 빌드 정보, 의존성 목록
- **기능 목록**: 지원 가속기, 인프라, 내보내기 포맷, 데이터 소스

### 📊 실제 데이터

현재 시스템에서 제공하는 실제 전력 데이터:
- **medgew01 노드**: ~183W (Kepler)
- **medgew02 노드**: ~281W (Kepler)
  - GPU-0 (NVIDIA A30): ~28W (DCGM)
  - GPU-1 (NVIDIA A30): ~28W (DCGM)
- **총 전력 소비**: ~464W

## 빠른 시작

### 1. 환경 설정

Python 3.12가 설치되어 있고 가상환경이 이미 구성되어 있습니다.

```bash
# 의존성 설치 (이미 완료됨)
.venv/Scripts/pip install -r requirements.txt
```

### 2. 서버 실행

```bash
# 개발 서버 실행
.venv/Scripts/python -m uvicorn app.main:app --port 8001 --host 127.0.0.1

# 또는 백그라운드 실행
.venv/Scripts/python -m uvicorn app.main:app --port 8001 --host 127.0.0.1 &
```

### 3. API 테스트

```bash
# 건강상태 확인
curl http://127.0.0.1:8001/api/v1/health

# 전체 GPU 전력 데이터 조회
curl -u admin:changeme http://127.0.0.1:8001/api/v1/power/gpu

# 특정 노드 데이터 조회
curl -u admin:changeme http://127.0.0.1:8001/api/v1/power/gpu/medgew01

# 데이터 내보내기 (JSON)
curl -u admin:changeme "http://127.0.0.1:8001/api/v1/power/export?period=1h&format=json"

# 데이터 내보내기 (CSV)
curl -u admin:changeme "http://127.0.0.1:8001/api/v1/power/export?period=1h&format=csv"
```

## API 엔드포인트 (7-Domain Architecture)

> **📋 상세 매핑 문서**: [docs/API_ENDPOINT_MAPPING.md](docs/API_ENDPOINT_MAPPING.md)
> 
> 모든 엔드포인트의 구현 상태, 데이터 소스, 필요한 Exporter 설정을 확인하세요.

### 공개 엔드포인트 (No Authentication)

| 엔드포인트 | 메서드 | 설명 | 구현 상태 |
|------------|--------|------|-----------|
| `/` | GET | 기본 메시지 및 API 정보 | ✅ |
| `/api/v1/auth/login` | POST | JWT 토큰 발급 | ✅ |
| `/api/v1/system/health` | GET | 시스템 건강상태 체크 | ✅ |
| `/api/v1/system/version` | GET | API 버전 및 의존성 정보 | ✅ |
| `/api/v1/system/capabilities` | GET | 지원 기능 목록 | ✅ |
| `/docs` | GET | FastAPI Swagger UI (API 문서) | ✅ |
| `/redoc` | GET | ReDoc API 문서 | ✅ |

### 1. Accelerators (가속기)

#### GPU 모니터링 (✅ 완료 - DCGM 기반)

| 엔드포인트 | 설명 | 데이터 소스 |
|------------|------|-------------|
| `GET /api/v1/accelerators/gpus` | GPU 목록 조회 | DCGM + Kepler |
| `GET /api/v1/accelerators/gpus/{gpu_id}` | GPU 상세 정보 | DCGM |
| `GET /api/v1/accelerators/gpus/{gpu_id}/metrics` | GPU 성능 메트릭 | DCGM |
| `GET /api/v1/accelerators/gpus/{gpu_id}/power` | GPU 전력 데이터 | DCGM |
| `GET /api/v1/accelerators/gpus/{gpu_id}/temperature` | GPU 온도 모니터링 | DCGM |
| `GET /api/v1/accelerators/gpus/summary` | GPU 전체 요약 | DCGM |

#### NPU 모니터링 (⚠️ Placeholder - Exporter 설정 필요)

| 엔드포인트 | 설명 | 데이터 소스 |
|------------|------|-------------|
| `GET /api/v1/accelerators/npus` | NPU 목록 조회 | NPU Exporter |
| `GET /api/v1/accelerators/npus/{npu_id}` | NPU 상세 정보 | NPU Exporter |
| `GET /api/v1/accelerators/npus/{npu_id}/metrics` | NPU 성능 메트릭 | NPU Exporter |
| `GET /api/v1/accelerators/npus/{npu_id}/cores` | NPU 코어 상태 (Furiosa) | NPU Exporter |
| `GET /api/v1/accelerators/npus/summary` | NPU 전체 요약 | NPU Exporter |

#### 통합 가속기 (✅ 완료)

| 엔드포인트 | 설명 | 데이터 소스 |
|------------|------|-------------|
| `GET /api/v1/accelerators/all` | 모든 가속기 통합 조회 | DCGM + NPU |
| `GET /api/v1/accelerators/summary` | 가속기 전체 요약 | DCGM + NPU |

### 2. Infrastructure (인프라)

#### Nodes (✅ 완료 - Kepler 기반)

| 엔드포인트 | 설명 | 데이터 소스 |
|------------|------|-------------|
| `GET /api/v1/infrastructure/nodes` | 노드 목록 | Kepler + kube_node_info |
| `GET /api/v1/infrastructure/nodes/{node_name}` | 노드 상세 | Kepler + kube_node_info |
| `GET /api/v1/infrastructure/nodes/{node_name}/power` | 노드 전력 | Kepler |
| `GET /api/v1/infrastructure/nodes/{node_name}/metrics` | 노드 메트릭 | Kepler |
| `GET /api/v1/infrastructure/nodes/summary` | 노드 요약 | Kepler |

#### Pods (✅ 완료 - Kepler 기반)

| 엔드포인트 | 설명 | 데이터 소스 |
|------------|------|-------------|
| `GET /api/v1/infrastructure/pods` | Pod 목록 | Kepler + kube_pod_info |
| `GET /api/v1/infrastructure/pods/{namespace}/{pod_name}` | Pod 상세 | Kepler + kube_pod_info |
| `GET /api/v1/infrastructure/pods/{namespace}/{pod_name}/power` | Pod 전력 | Kepler |
| `GET /api/v1/infrastructure/pods/summary` | Pod 요약 | Kepler |

#### Containers (✅ 완료 - Kepler 기반)

| 엔드포인트 | 설명 | 데이터 소스 |
|------------|------|-------------|
| `GET /api/v1/infrastructure/containers` | 컨테이너 목록 | Kepler |
| `GET /api/v1/infrastructure/containers/{container_id}` | 컨테이너 상세 | Kepler |
| `GET /api/v1/infrastructure/containers/{container_id}/metrics` | 컨테이너 메트릭 | Kepler |

#### VMs (❌ 미구현 - OpenStack 연동 필요)

| 엔드포인트 | 설명 | 데이터 소스 |
|------------|------|-------------|
| `GET /api/v1/infrastructure/vms` | VM 목록 | OpenStack |
| `GET /api/v1/infrastructure/vms/{vm_id}` | VM 상세 | OpenStack |
| `GET /api/v1/infrastructure/vms/{vm_id}/power` | VM 전력 | OpenStack |
| `GET /api/v1/infrastructure/vms/{vm_id}/metrics` | VM 메트릭 | OpenStack |

### 3. Hardware (하드웨어)

#### IPMI 센서 (⚠️ API 완료 - Exporter 설정 필요)

| 엔드포인트 | 설명 | 데이터 소스 |
|------------|------|-------------|
| `GET /api/v1/hardware/ipmi/sensors` | 전체 IPMI 센서 | IPMI Exporter |
| `GET /api/v1/hardware/ipmi/sensors/{node_name}` | 노드별 센서 | IPMI Exporter |
| `GET /api/v1/hardware/ipmi/power` | IPMI 전력 센서 | IPMI Exporter |
| `GET /api/v1/hardware/ipmi/temperature` | IPMI 온도 센서 | IPMI Exporter |
| `GET /api/v1/hardware/ipmi/fans` | IPMI 팬 속도 | IPMI Exporter |
| `GET /api/v1/hardware/ipmi/voltage` | IPMI 전압 센서 | IPMI Exporter |
| `GET /api/v1/hardware/ipmi/summary` | IPMI 종합 요약 | IPMI Exporter |

> **참고**: IPMI API는 구현되었으나, 각 노드에 IPMI Exporter 설치 및 Prometheus 연동이 필요합니다.

### 4. Clusters (클러스터) - ✅ 완료

| 엔드포인트 | 설명 | 데이터 소스 |
|------------|------|-------------|
| `GET /api/v1/clusters` | 클러스터 목록 | 환경 변수 |
| `GET /api/v1/clusters/{cluster_name}` | 클러스터 상세 | Prometheus |
| `GET /api/v1/clusters/{cluster_name}/summary` | 클러스터 요약 | Prometheus |
| `GET /api/v1/clusters/{cluster_name}/topology` | 클러스터 토폴로지 | kube_* metrics |
| `GET /api/v1/clusters/{cluster_name}/power` | 클러스터 전력 | Kepler + DCGM |

> **참고**: 멀티 클러스터 사용 시 `PROMETHEUS_CLUSTERS` 환경 변수 설정 필요

### 5. Monitoring (통합 모니터링) - ✅ 완료

#### 통합 전력 모니터링

| 엔드포인트 | 설명 | 데이터 소스 |
|------------|------|-------------|
| `GET /api/v1/monitoring/power` | 통합 전력 소비 | Kepler + DCGM |
| `GET /api/v1/monitoring/power/accelerators` | 가속기 전력 | DCGM + NPU |
| `GET /api/v1/monitoring/power/infrastructure` | 인프라 전력 | Kepler |
| `GET /api/v1/monitoring/power/breakdown` | 전력 분해 분석 | Kepler + DCGM |
| `GET /api/v1/monitoring/power/efficiency` | 전력 효율성 (PUE) | Kepler + IPMI |

#### 시계열 데이터

| 엔드포인트 | 설명 | 데이터 소스 |
|------------|------|-------------|
| `GET /api/v1/monitoring/timeseries/power` | 전력 시계열 | Prometheus |
| `GET /api/v1/monitoring/timeseries/metrics` | 메트릭 시계열 | Prometheus |
| `GET /api/v1/monitoring/timeseries/temperature` | 온도 시계열 | Prometheus |

#### 실시간 스트리밍

| 엔드포인트 | 프로토콜 | 설명 | 데이터 소스 |
|------------|----------|------|-------------|
| `/api/v1/monitoring/stream/power` | WebSocket | 전력 실시간 스트림 | Prometheus |
| `/api/v1/monitoring/stream/metrics` | WebSocket | 메트릭 실시간 스트림 | Prometheus |
| `/api/v1/monitoring/events/power` | SSE | 전력 이벤트 스트림 | Prometheus |
| `GET /api/v1/monitoring/stream/info` | HTTP | 스트리밍 정보 | - |

### 6. Export (데이터 내보내기) - ✅ 완료

| 엔드포인트 | 설명 | 지원 포맷 |
|------------|------|-----------|
| `GET /api/v1/export/power` | 전력 데이터 내보내기 | JSON, CSV, Parquet, Excel, PDF |
| `GET /api/v1/export/metrics` | 메트릭 데이터 내보내기 | JSON, CSV, Parquet, Excel, PDF |
| `GET /api/v1/export/report` | 종합 리포트 생성 | PDF, Excel |
| `GET /api/v1/export/formats` | 지원 포맷 목록 | - |
| `GET /api/v1/export/templates` | 리포트 템플릿 목록 | - |

**지원 포맷**: JSON (기본), CSV, Parquet (pyarrow), Excel (openpyxl), PDF (reportlab)
**리포트 템플릿**: Daily, Weekly, Monthly, Custom

### 7. System (시스템 정보) - ✅ 완료

| 엔드포인트 | 설명 | 인증 필요 |
|------------|------|-----------|
| `GET /api/v1/system/health` | 헬스체크 | ❌ |
| `GET /api/v1/system/info` | 시스템 정보 | ✅ |
| `GET /api/v1/system/version` | 버전 정보 | ❌ |
| `GET /api/v1/system/capabilities` | 지원 기능 목록 | ❌ |
| `GET /api/v1/system/metrics` | API 서버 메트릭 (Prometheus) | ❌ |
| `GET /api/v1/system/status` | 종합 상태 | ❌ |

### Legacy Endpoints (하위 호환성, 향후 제거 예정)

> **⚠️ Deprecated**: 이 엔드포인트들은 하위 호환성을 위해 유지되지만, 향후 버전에서 제거될 예정입니다.

| Legacy 엔드포인트 | 새 엔드포인트 | 상태 |
|-------------------|---------------|------|
| `GET /api/v1/gpu/info` | `/api/v1/accelerators/gpus` | ⚠️ Deprecated |
| `GET /api/v1/gpu/metrics` | `/api/v1/accelerators/gpus/{gpu_id}/metrics` | ⚠️ Deprecated |
| `GET /api/v1/gpu/summary` | `/api/v1/accelerators/gpus/summary` | ⚠️ Deprecated |
| `GET /api/v1/power/gpu` | `/api/v1/monitoring/power?resource_type=gpus` | ⚠️ Deprecated |
| `GET /api/v1/power/pods` | `/api/v1/infrastructure/pods` | ⚠️ Deprecated |
| `GET /api/v1/cluster/info` | `/api/v1/clusters/{cluster_name}` | ⚠️ Deprecated |
| `GET /api/v1/power/cluster/total` | `/api/v1/monitoring/power` | ⚠️ Deprecated |
| `GET /api/v1/health` | `/api/v1/system/health` | ⚠️ Deprecated |

### 응답 예시

#### GPU 전력 데이터 (`/api/v1/power/gpu`)

```json
{
  "timestamp": "2025-09-26T14:21:06.783023",
  "period": "1h",
  "total_gpus": 2,
  "gpus": [
    {
      "gpu_id": "Package-energy1",
      "instance": "medgew01",
      "power_draw_watts": 183.0,
      "utilization_percent": 0.0,
      "temperature_celsius": 0.0,
      "memory_used_mb": 0,
      "memory_total_mb": 0
    },
    {
      "gpu_id": "Package-energy1",
      "instance": "medgew02",
      "power_draw_watts": 280.6,
      "utilization_percent": 0.0,
      "temperature_celsius": 0.0,
      "memory_used_mb": 0,
      "memory_total_mb": 0
    }
  ],
  "summary": {
    "total_power_watts": 463.6,
    "avg_power_watts": 231.8,
    "max_power_watts": 280.6,
    "avg_utilization_percent": 0.0
  }
}
```

## 환경 설정

### 환경 변수 (.env)

```bash
# 애플리케이션 설정
APP_NAME="AI Accelerator & Infrastructure Monitor"
HOST="127.0.0.1"
PORT=8001
LOG_LEVEL="INFO"

# 인증 설정
API_AUTH_USERNAME="admin"
API_AUTH_PASSWORD="changeme"

# Prometheus 연결 (단일 클러스터)
PROMETHEUS_URL="http://101.79.0.107:30090"
PROMETHEUS_TIMEOUT=30

# 멀티 클러스터 설정 (옵션, JSON 형식)
# PROMETHEUS_CLUSTERS='[
#   {"name": "cluster1", "url": "http://prometheus1.example.com", "region": "asia-northeast3", "description": "Production Cluster 1"},
#   {"name": "cluster2", "url": "http://prometheus2.example.com", "region": "us-central1", "description": "Production Cluster 2"}
# ]'

# 캐싱 설정 (초 단위)
CACHE_TTL_GPU_CURRENT=30           # GPU 실시간 데이터
CACHE_TTL_GPU_TIMESERIES=300       # 시계열 데이터
CACHE_TTL_POWER_SUMMARY=300        # 전력 요약 데이터
CACHE_TTL_CLUSTER_INFO=60          # 클러스터 정보
```

### 멀티 클러스터 설정

여러 Kubernetes 클러스터를 통합 모니터링하려면 `PROMETHEUS_CLUSTERS` 환경 변수를 JSON 배열로 설정:

```json
[
  {
    "name": "cluster1",
    "url": "http://prometheus1.example.com:9090",
    "region": "asia-northeast3",
    "description": "Production Cluster 1 (GPU Workloads)"
  },
  {
    "name": "cluster2",
    "url": "http://prometheus2.example.com:9090",
    "region": "us-central1",
    "description": "Production Cluster 2 (NPU Workloads)"
  }
]
```

**클러스터 필터링**:
- `?cluster=cluster1` 쿼리 파라미터로 특정 클러스터 데이터 조회
- 파라미터 생략 시 기본 클러스터 또는 전체 클러스터 데이터 반환

## 아키텍처

### 시스템 구성도 (7-Domain Architecture)

```
┌──────────────────────────────────────────────────────────────────────┐
│                           Client Applications                         │
│   (Dashboard, CLI, WebSocket Clients, Export Tools, Alerting)        │
└────────────────────────────┬─────────────────────────────────────────┘
                             │ REST API / WebSocket / SSE
┌────────────────────────────┴─────────────────────────────────────────┐
│                       FastAPI Application                             │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                   7-Domain Router Layer                       │   │
│  │  Accelerators│Infrastructure│Hardware│Clusters│Monitoring│   │   │
│  │  Export│System                                               │   │
│  └──────────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │            Service Layer (CRUD + Collectors)                  │   │
│  │  DCGM│Kepler│IPMI│NPU│OpenStack│Stream│Export│Cache         │   │
│  └──────────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │      Middleware (Metrics, Auth, CORS, Error Handling)        │   │
│  └──────────────────────────────────────────────────────────────┘   │
└────────────────────────────┬─────────────────────────────────────────┘
                             │ Prometheus Query API
┌────────────────────────────┴─────────────────────────────────────────┐
│                         Prometheus Cluster                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │  Cluster 1   │  │  Cluster 2   │  │  Cluster N   │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
└────────────────────────────┬─────────────────────────────────────────┘
                             │ Metrics Collection
┌────────────────────────────┴─────────────────────────────────────────┐
│                        Metric Exporters                               │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐      │
│  │  DCGM   │ │ Kepler  │ │  IPMI   │ │   NPU   │ │OpenStack│      │
│  │Exporter │ │Exporter │ │Exporter │ │Exporter │ │Telegraf │      │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘      │
└────────────────────────────┬─────────────────────────────────────────┘
                             │ Hardware Metrics
┌────────────────────────────┴─────────────────────────────────────────┐
│                     Physical Infrastructure                           │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐      │
│  │   GPU   │ │   NPU   │ │  Nodes  │ │  Pods   │ │   VMs   │      │
│  │(NVIDIA) │ │(Furiosa)│ │ (K8s)   │ │ (K8s)   │ │(OpenSt) │      │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘      │
└───────────────────────────────────────────────────────────────────────┘
```

### 7-Domain 설계 철학

1. **Accelerators**: AI 가속기 전용 모니터링 (GPU, NPU, 향후 TPU/IPU 등)
2. **Infrastructure**: 인프라 리소스 모니터링 (Nodes, Pods, Containers, VMs)
3. **Hardware**: 물리 하드웨어 센서 모니터링 (IPMI 전력/온도/팬/전압)
4. **Clusters**: 멀티 클러스터 관리 및 클러스터별 리소스 집계
5. **Monitoring**: 크로스 도메인 통합 모니터링 (전력, 시계열, 실시간 스트리밍, 효율성 지표)
6. **Export**: 데이터 내보내기 (JSON, CSV, Parquet, Excel, PDF, 리포트)
7. **System**: 시스템 정보 및 메트릭 (헬스체크, API 메트릭, 버전 정보)

### 주요 구성 요소

- **FastAPI**: 고성능 비동기 웹 프레임워크
- **Pydantic**: 데이터 검증 및 직렬화
- **Prometheus Client**: 메트릭 데이터 수집 및 쿼리
- **Data Sources**:
  - **DCGM**: NVIDIA GPU 상세 모니터링
  - **Kepler**: 노드/Pod 레벨 전력 분해
  - **IPMI Exporter**: 물리 서버 센서 데이터
  - **NPU Exporters**: Furiosa/Rebellions NPU 메트릭 (Placeholder)
  - **OpenStack Telemetry**: VM 메트릭 (Placeholder)
- **Authentication**: Basic Authentication
- **Caching**: In-Memory TTL 기반 캐싱 (30초~1시간)
- **Metrics Middleware**: API 서버 메트릭 수집 (Prometheus 형식)
- **Stream Service**: WebSocket 및 SSE 실시간 데이터 스트리밍
- **Export Service**: 다양한 포맷 데이터 내보내기

## 개발 정보

### 프로젝트 구조 (7-Domain Architecture)

```
ai-chip-monitor/
├── .venv/                              # Python 가상환경
├── app/                                # FastAPI 애플리케이션
│   ├── api/                            # API 라우터 (7-Domain)
│   │   ├── v1/                         # API v1
│   │   │   ├── accelerators.py         # Accelerators 도메인 (GPU, NPU)
│   │   │   ├── infrastructure.py       # Infrastructure 도메인 (Nodes, Pods, VMs)
│   │   │   ├── hardware.py             # Hardware 도메인 (IPMI)
│   │   │   ├── clusters.py             # Clusters 도메인 (멀티 클러스터)
│   │   │   ├── monitoring.py           # Monitoring 도메인 (통합 전력, 시계열, 스트리밍)
│   │   │   ├── export.py               # Export 도메인 (데이터 내보내기)
│   │   │   └── system.py               # System 도메인 (헬스체크, 메트릭)
│   │   ├── gpu.py                      # Legacy GPU API (하위 호환성)
│   │   └── cluster.py                  # Legacy Cluster API (하위 호환성)
│   ├── models/                         # Pydantic 데이터 모델
│   │   ├── accelerators/               # 가속기 모델
│   │   │   ├── gpu.py                  # GPU 모델
│   │   │   ├── npu.py                  # NPU 모델
│   │   │   └── common.py               # 공통 가속기 모델
│   │   ├── infrastructure/             # 인프라 모델
│   │   │   ├── nodes.py                # 노드 모델
│   │   │   ├── pods.py                 # Pod 모델
│   │   │   ├── containers.py           # 컨테이너 모델
│   │   │   └── vms.py                  # VM 모델
│   │   ├── hardware/                   # 하드웨어 모델
│   │   │   └── ipmi.py                 # IPMI 센서 모델
│   │   └── common/                     # 공통 모델
│   │       ├── responses.py            # 응답 모델
│   │       ├── queries.py              # 쿼리 파라미터 모델
│   │       └── enums.py                # 열거형 모델
│   ├── services/                       # 서비스 레이어
│   │   ├── prometheus.py               # Prometheus 클라이언트 서비스
│   │   ├── cache_service.py            # 캐시 서비스
│   │   ├── stream.py                   # WebSocket/SSE 스트리밍 서비스
│   │   ├── cluster_registry.py         # 클러스터 레지스트리
│   │   ├── collectors/                 # 데이터 수집기
│   │   │   └── ipmi.py                 # IPMI Collector (Placeholder)
│   │   └── exporters/                  # 데이터 내보내기
│   │       ├── __init__.py             # Exporter 패키지
│   │       ├── csv_exporter.py         # CSV 내보내기
│   │       ├── parquet_exporter.py     # Parquet 내보내기
│   │       ├── excel_exporter.py       # Excel 내보내기
│   │       └── pdf_exporter.py         # PDF 리포트 생성
│   ├── middleware/                     # 미들웨어
│   │   ├── __init__.py                 # 미들웨어 패키지
│   │   └── metrics.py                  # API 메트릭 미들웨어
│   ├── auth.py                         # 인증 시스템
│   ├── config.py                       # 환경 설정 관리
│   ├── main.py                         # 애플리케이션 진입점
│   └── crud.py                         # 데이터 처리 로직 (3300+ 라인)
├── spec/                               # 설계 문서
│   ├── ARCHITECTURE.md                 # 7-도메인 아키텍처 상세 설계
│   ├── API_SPECIFICATION.md            # API 명세서 (완전판)
│   ├── DATA_MODELS.md                  # 데이터 모델 명세
│   ├── PROMETHEUS_SETUP.md             # Prometheus Exporter 설정 가이드
│   └── tasks.md                        # 구현 작업 진행표
├── tests/                              # 테스트 코드 (구현 예정)
│   ├── api/                            # API 엔드포인트 테스트
│   ├── services/                       # 서비스 테스트
│   └── models/                         # 모델 테스트
├── .env                                # 환경 변수
├── requirements.txt                    # Python 의존성
├── README.md                           # 프로젝트 개요 (본 파일)
└── CLAUDE.md                           # Claude 개발 가이드
```

### 의존성

#### 핵심 프레임워크
- **fastapi**: 고성능 웹 프레임워크 (0.104.1+)
- **uvicorn**: ASGI 서버 (0.24.0+)
- **pydantic**: 데이터 검증 및 직렬화 (2.5.0+)
- **pydantic-settings**: 환경 설정 관리

#### HTTP 및 통신
- **requests**: Prometheus HTTP 클라이언트
- **python-multipart**: Form 데이터 파싱

#### 메트릭 및 모니터링
- **prometheus-client**: API 서버 메트릭 수집 및 노출

#### 데이터 내보내기
- **pyarrow**: Parquet 파일 생성
- **openpyxl**: Excel 파일 생성
- **reportlab**: PDF 리포트 생성

### 테스트

```bash
# 단위 테스트 실행
.venv/Scripts/python -m pytest tests/

# 특정 테스트 실행
.venv/Scripts/python -m pytest tests/test_api.py -v
```

## 모니터링 및 운영

### 건강상태 확인

```bash
curl http://127.0.0.1:8001/api/v1/health
```

응답:
```json
{
  "timestamp": "2025-09-26T14:21:45.400654",
  "status": "healthy",
  "version": "1.0.0",
  "prometheus": {
    "status": "connected",
    "url": "http://101.79.0.107:30090"
  },
  "cache": {
    "status": "active",
    "entries": 1
  }
}
```

### 로그 확인

서버 실행 시 콘솔에서 로그를 확인할 수 있습니다:
- INFO: 일반적인 요청 로그
- ERROR: 오류 발생 시 상세 정보
- WARNING: 경고 메시지

## 문제 해결

### 일반적인 문제들

1. **Prometheus 연결 실패**
   - URL이 올바른지 확인: `http://101.79.0.107:30090`
   - 네트워크 연결 상태 확인

2. **인증 실패 (401)**
   - 사용자명: `admin`
   - 비밀번호: `changeme`

3. **포트 충돌**
   - 다른 포트 사용: `--port 8002`

4. **캐시 문제**
   - 30초 후 자동 만료됨
   - 서버 재시작으로 캐시 초기화

## 개발 상태

### 구현 완료 (Phase 1-9) ✅
- ✅ Phase 0: 기본 인프라 (완료)
- ✅ Phase 1: GPU 모니터링 (Kepler + DCGM, 완료)
- ✅ Phase 2: 7-도메인 아키텍처 재구성 (완료)
- ✅ Phase 3: Accelerators 도메인 (GPU 완료, NPU Placeholder)
- ✅ Phase 4: Infrastructure 도메인 (Nodes, Pods, Containers 완료, VMs Placeholder)
- ✅ Phase 5: Hardware 도메인 (IPMI API 완료, Exporter 설정 필요)
- ✅ Phase 6: Clusters 도메인 (멀티 클러스터 프레임워크 완료)
- ✅ Phase 7: Monitoring 도메인 (통합 전력, 시계열, WebSocket/SSE 완료)
- ✅ Phase 8: Export 도메인 (JSON, CSV, Parquet, Excel, PDF 완료)
- ✅ Phase 9: System 도메인 (헬스체크, 메트릭, 버전 정보 완료)

### 진행 중 (Phase 10) 🚧
- 🚧 Phase 10: 테스트 및 문서화
  - ✅ README.md 업데이트 (7-도메인 아키텍처 반영)
  - ✅ spec/DATA_MODELS.md 업데이트 완료
  - ⏳ 단위 테스트 작성 (Collectors, Services)
  - ⏳ API 엔드포인트 통합 테스트
  - ⏳ spec/PROMETHEUS_SETUP.md 업데이트 (NPU, IPMI Exporter)

### 향후 계획 (Phase 11) 📋
- Phase 11: 성능 최적화 및 프로덕션 준비
  - Prometheus 쿼리 병렬화
  - Rate Limiting 구현
  - 구조화된 로깅 (JSON)
  - Kubernetes 배포 YAML
  - CI/CD 파이프라인

### 구현 상태별 분류

#### ✅ 완전 구현 (데이터 소스 구성됨)
- **Authentication**: JWT 토큰 기반 인증
- **Accelerators - GPU**: DCGM 기반 완전 구현
- **Infrastructure - Nodes/Pods/Containers**: Kepler 기반 완전 구현
- **Clusters**: 멀티 클러스터 프레임워크 완전 구현
- **Monitoring**: 통합 전력, 시계열, 실시간 스트리밍 완전 구현
- **Export**: 모든 포맷 (JSON, CSV, Parquet, Excel, PDF) 완전 구현
- **System**: 헬스체크, 메트릭, 버전 정보 완전 구현

#### ⚠️ API 완료, Exporter 설정 필요
- **Accelerators - NPU**: Furiosa AI / Rebellions NPU Exporter 연동 필요
- **Hardware - IPMI**: IPMI Exporter 설정 및 Prometheus 연동 필요

#### ❌ 미구현 (향후 계획)
- **Infrastructure - VMs**: OpenStack 연동 필요 (Telegraf 또는 OpenStack Exporter)

## 데이터 소스 및 Exporter 설정

### 구성된 데이터 소스 (✅)

| 데이터 소스 | 용도 | 상태 | Prometheus URL |
|-------------|------|------|----------------|
| **DCGM Exporter** | NVIDIA GPU 모니터링 | ✅ 구성됨 | http://101.79.0.107:30090 |
| **Kepler** | 노드/Pod/컨테이너 전력 분해 | ✅ 구성됨 | http://101.79.0.107:30090 |
| **Kubernetes Metrics** | 클러스터 메타데이터 | ✅ 구성됨 | http://101.79.0.107:30090 |

### 설정 필요한 데이터 소스 (⚠️)

| 데이터 소스 | 용도 | 상태 | 설정 가이드 |
|-------------|------|------|-------------|
| **IPMI Exporter** | 물리 서버 센서 (전력, 온도, 팬) | ⚠️ 설정 필요 | [PROMETHEUS_DCGM_SETUP.md](docs/PROMETHEUS_DCGM_SETUP.md) |
| **Furiosa NPU Exporter** | Furiosa AI NPU 모니터링 | ⚠️ 설정 필요 | [PROMETHEUS_DCGM_SETUP.md](docs/PROMETHEUS_DCGM_SETUP.md) |
| **Rebellions NPU Exporter** | Rebellions NPU 모니터링 | ⚠️ 설정 필요 | [PROMETHEUS_DCGM_SETUP.md](docs/PROMETHEUS_DCGM_SETUP.md) |

### 향후 계획 (❌)

| 데이터 소스 | 용도 | 상태 | 비고 |
|-------------|------|------|------|
| **OpenStack Exporter** | VM 모니터링 | ❌ 미구현 | Phase 4.4 (향후) |
| **Telegraf + OpenStack** | VM 메트릭 수집 | ❌ 미구현 | Phase 4.4 (향후) |

### Exporter 설정 빠른 가이드

#### IPMI Exporter 설정
```bash
# 각 노드에 IPMI Exporter 설치
# 1. BMC 접근 권한 확인
ipmitool sensor list

# 2. IPMI Exporter 설치 (Docker)
docker run -d --name ipmi-exporter \
  --net=host \
  -v /etc/ipmi_exporter:/config \
  prometheuscommunity/ipmi-exporter

# 3. Prometheus scrape 설정 추가
# 참고: docs/PROMETHEUS_DCGM_SETUP.md
```

#### NPU Exporter 설정
```bash
# Furiosa AI NPU Exporter 설치
# 1. Furiosa SDK 설치
# 2. NPU Exporter 설치
# 3. Prometheus scrape 설정 추가
# 참고: docs/PROMETHEUS_DCGM_SETUP.md
```

## 관련 문서

- **API 엔드포인트 매핑**: [docs/API_ENDPOINT_MAPPING.md](docs/API_ENDPOINT_MAPPING.md) - 전체 엔드포인트 구현 상태 및 데이터 소스
- **API 명세서**: [spec/API_SPECIFICATION.md](spec/API_SPECIFICATION.md) - 전체 엔드포인트 상세 문서
- **아키텍처 문서**: [spec/ARCHITECTURE.md](spec/ARCHITECTURE.md) - 7-도메인 설계 철학 및 구조
- **데이터 모델**: [spec/DATA_MODELS.md](spec/DATA_MODELS.md) - Pydantic 모델 명세
- **Prometheus 설정**: [docs/PROMETHEUS_DCGM_SETUP.md](docs/PROMETHEUS_DCGM_SETUP.md) - Exporter 설정 가이드
- **작업 진행표**: [spec/tasks.md](spec/tasks.md) - Phase별 체크리스트
- **개발 가이드**: [CLAUDE.md](CLAUDE.md) - Claude Code 개발 가이드

## 라이선스

이 프로젝트는 프로토타입 목적으로 개발되었습니다.

## 기여하기

버그 리포트나 기능 제안은 이슈를 통해 제출해 주세요.

---

## 빠른 참조

### 📚 주요 문서

| 문서 | 설명 | 링크 |
|------|------|------|
| **API 엔드포인트 매핑** | 전체 엔드포인트 구현 상태 및 데이터 소스 | [API_ENDPOINT_MAPPING.md](docs/API_ENDPOINT_MAPPING.md) |
| **API 구현 상태 요약** | 도메인별 구현 상태 및 필요 작업 (한글) | [API_구현상태_요약.md](docs/API_구현상태_요약.md) |
| **API 명세서** | 전체 엔드포인트 상세 문서 | [API_SPECIFICATION.md](spec/API_SPECIFICATION.md) |
| **아키텍처 문서** | 7-도메인 설계 철학 및 구조 | [ARCHITECTURE.md](spec/ARCHITECTURE.md) |
| **Prometheus 설정** | Exporter 설정 가이드 | [PROMETHEUS_DCGM_SETUP.md](docs/PROMETHEUS_DCGM_SETUP.md) |

### 🎯 구현 상태 요약

| 도메인 | 엔드포인트 수 | 구현 상태 | 데이터 소스 |
|--------|---------------|-----------|-------------|
| **Authentication** | 3 | ✅ 100% | 환경 변수 |
| **Accelerators - GPU** | 6 | ✅ 100% | DCGM |
| **Accelerators - NPU** | 5 | ⚠️ API 완료 | NPU Exporter 필요 |
| **Infrastructure - Nodes** | 5 | ✅ 100% | Kepler |
| **Infrastructure - Pods** | 4 | ✅ 100% | Kepler |
| **Infrastructure - Containers** | 3 | ✅ 100% | Kepler |
| **Infrastructure - VMs** | 5 | ❌ 미구현 | OpenStack 필요 |
| **Hardware - IPMI** | 7 | ⚠️ API 완료 | IPMI Exporter 필요 |
| **Clusters** | 5 | ✅ 100% | Prometheus |
| **Monitoring** | 12 | ✅ 100% | Prometheus |
| **Export** | 5 | ✅ 100% | - |
| **System** | 6 | ✅ 100% | - |
| **Legacy API** | 13 | ✅ 100% (Deprecated) | - |

**전체 구현률**: ~90% (85% 완전 구현 + 5% API 완료)

### 🔧 필요한 작업

1. **IPMI Exporter 설정** (우선순위: 높음)
   - 물리 서버 센서 모니터링 활성화
   - 예상 소요 시간: 2-4시간

2. **NPU Exporter 설정** (우선순위: 중간)
   - Furiosa AI / Rebellions NPU 모니터링 활성화
   - 예상 소요 시간: 4-8시간

3. **OpenStack VM 모니터링** (우선순위: 낮음, 향후 계획)
   - VM 모니터링 구현
   - 예상 소요 시간: 8-16시간

---

**프로젝트**: AI Accelerator & Infrastructure Monitoring API
**API 버전**: 2.0.0
**아키텍처**: 7-Domain Design
**개발 상태**: Phase 10 진행 중 (테스트 및 문서화)
**전체 구현률**: ~90%
**마지막 업데이트**: 2025-01-20
