"""KCloud Monitor API v2"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v2 import (
    accelerators,
    auth,
    clusters,
    export,
    logs,
    monitoring,
    nodes,
    openstack,
    resource_map,
    storage,
    system,
    workloads,
    workloads_global,
)
from app.auth import verify_token_or_api_key
from app.config import settings
from app.logging_config import configure_logging

from app.middleware import MetricsMiddleware, RateLimitMiddleware, RequestIDMiddleware

APP_VERSION = "0.2.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.LOG_LEVEL)
    print("KCloud Monitor API v2 - Starting up")
    print(f"Version: {APP_VERSION} | Docs: /docs | Metrics: /api/v2/system/metrics")
    yield
    print("Application shutdown")


API_DESCRIPTION = """
KCloud Monitor API v2

이기종 AI 반도체(NVIDIA GPU, Furiosa NPU, Rebellions NPU) 인프라의 자원, 전력, 워크로드 모니터링 API.

## 경로 구조
- `/clusters` : 클러스터와 그 하위 노드, 가속기, 파티션의 상태와 메트릭 조회
- `/workloads` : 클러스터 구분 없이 전체 Pod와 서비스를 한 번에 조회
- `/monitoring` : 시스템 전체 현황, 전력 합계, 메트릭 시계열, 실시간 스트림(SSE) 조회
- `/storage` : Ceph 분산 스토리지 상태 조회
- `/openstack` : 하이퍼바이저, VM, 프로젝트 등 OpenStack 자원 조회
- `/logs` : Loki 기반 로그 검색과 실시간 스트리밍
- `/export` : 전력, 메트릭, 리포트 데이터를 CSV 또는 JSON으로 내보내기
- `/resource-map` : 가속기가 어떤 VM, 어떤 Pod에 배정되어 있는지 추적
- `/system` : 서비스 헬스, 버전, Prometheus 메트릭
- `/auth` : 토큰 발급과 검증

## 공통 응답
- `status` : `success` | `partial` | `error` | `not_implemented`
- `observed_at` : 데이터 관측 시각
- `warnings` : 일부 데이터가 빈 경우의 원인 코드 목록
- 에러 응답 : `{status, error:{code, message, retryable}, request_id, observed_at}`

## 인증
- JWT Bearer 토큰 : `POST /api/v2/auth/login` 으로 발급
- `X-API-Key` 헤더 : 서버 간 호출용
- `/system` 하위 경로(헬스, 버전, 메트릭)는 인증 없이 접근 가능
"""

app = FastAPI(
    title="KCloud Monitor API",
    description=API_DESCRIPTION,
    version=APP_VERSION,
    lifespan=lifespan,
)

# ============================================================================
# Middleware
# ============================================================================


cors_allow_origins = [o.strip() for o in settings.CORS_ALLOW_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(MetricsMiddleware)
app.add_middleware(RateLimitMiddleware, limit_per_minute=settings.RATE_LIMIT_PER_MINUTE)
app.add_middleware(RequestIDMiddleware)

# ============================================================================
# Exception Handlers
# ============================================================================



def _error_body(request: Request, code: str, message: str, retryable: bool = False) -> dict:
    return {
        "status": "error",
        "error": {"code": code, "message": message, "retryable": retryable},
        "request_id": getattr(request.state, "request_id", None),
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_error_body(request, "VALIDATION_ERROR", str(exc)),
    )


# ============================================================================
# API v2 Routers
# ============================================================================

PROTECTED = [Depends(verify_token_or_api_key)]
V2 = "/api/v2"

app.include_router(auth.router, prefix=V2, tags=["Authentication"])
app.include_router(system.router, prefix=V2, tags=["System"])
app.include_router(clusters.router, prefix=V2, tags=["Clusters"], dependencies=PROTECTED)
app.include_router(nodes.router, prefix=V2, tags=["Nodes"], dependencies=PROTECTED)
app.include_router(accelerators.router, prefix=V2, tags=["Accelerators"], dependencies=PROTECTED)
app.include_router(storage.router, prefix=V2, tags=["Storage"], dependencies=PROTECTED)
app.include_router(openstack.router, prefix=V2, tags=["OpenStack"], dependencies=PROTECTED)
app.include_router(workloads.router, prefix=V2, tags=["Workloads"], dependencies=PROTECTED)
app.include_router(workloads_global.router, prefix=V2, tags=["Workloads Global"], dependencies=PROTECTED)
app.include_router(monitoring.router, prefix=V2, tags=["Monitoring"], dependencies=PROTECTED)
app.include_router(logs.router, prefix=V2, tags=["Logs"], dependencies=PROTECTED)
app.include_router(export.router, prefix=V2, tags=["Export"], dependencies=PROTECTED)
app.include_router(resource_map.router, prefix=V2, tags=["Resource Map"], dependencies=PROTECTED)

# ============================================================================
# Root
# ============================================================================


@app.get("/")
def read_root():
    """API 진입점. 주요 경로 안내

    - service, version : 서비스 이름과 버전
    - docs : Swagger 문서 경로
    - endpoints : 도메인별 대표 경로와 한 줄 설명
    """
    return {
        "service": "KCloud Monitor API",
        "version": APP_VERSION,
        "docs": "/docs",
        "endpoints": {
            "auth": "POST /api/v2/auth/login - 토큰 발급",
            "clusters": "GET /api/v2/clusters - 클러스터 목록과 상태",
            "nodes": "GET /api/v2/clusters/{cluster}/nodes - 노드 자원 정보",
            "accelerators": "GET /api/v2/clusters/{cluster}/nodes/{node}/accelerators - 가속기 상태",
            "workloads": "GET /api/v2/workloads/pods - 전체 Pod 목록",
            "monitoring": "GET /api/v2/monitoring/overview - 전체 시스템 현황",
            "storage": "GET /api/v2/clusters/{cluster}/storage/summary - 스토리지 자원",
            "openstack": "GET /api/v2/openstack/summary - OpenStack 자원",
            "logs": "GET /api/v2/logs/search - 로그 검색",
            "export": "GET /api/v2/export/power - 데이터 내보내기",
            "resource_map": "GET /api/v2/resource-map/relationships - 자원 간 연결 관계",
            "system": "GET /api/v2/system/health - 서비스 헬스체크",
        },
    }
