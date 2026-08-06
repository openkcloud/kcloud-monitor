"""
KCloud Monitor API — v2 스캐폴드 엔트리포인트.

v1(프로토타입)은 종료되었다(design_contracts §1 "v1 종료, v2 신규 개발").
현 단계는 라우팅·인증·경로 구조만 확정하고 전 엔드포인트가 스텁으로 응답한다.
경로 SoT: sample_api.md(Monitor 81 + Resource-Map 8) + storage_ceph_plan(S1~S10) + 별칭 4.
"""
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
    print("KCloud Monitor API (v2 scaffold) - Starting up")
    print(f"Version: {APP_VERSION} | Docs: /docs | Metrics: /api/v2/system/metrics")
    yield
    print("Application shutdown")


API_DESCRIPTION = """
**KCloud Monitor API v2** — 이기종 AI 반도체 인프라 모니터링 (OPT.001/OPT.002).

## 현재 단계: 스캐폴드
라우팅·인증·경로 구조만 확정된 상태로, **모든 엔드포인트는 스텁 응답**
(`status: not_implemented`, 정의·데이터소스·설계 참조 포함)을 반환한다.

## 경로 구조 (sample_api.md)
- `/clusters/{c}/...` — 계층형 canonical (노드→가속기→파티션, storage/ceph, openstack, workloads)
- `/workloads/...` — 포탈용 전역 진입점 (`_links.canonical` 포함)
- `/monitoring/...` — 횡단 집계 (전력 P1~P8, SSE 스트림)
- `/resource-map/...` — 자원 계보 원장 (GPU→VM→Pod 교차 추적)

## 공통 응답 정책 (design_contracts §6)
- `status`: `success` | `partial` | `error` (스텁 단계: `not_implemented`)
- 모든 응답에 `observed_at`, `is_stale`, 경고 시 `warnings[]` / `partial_sources[]`
- 에러 스키마: `{status, error:{code, message, retryable}, request_id, observed_at}`

## 인증
- JWT Bearer(`POST /api/v2/auth/login`) 또는 `X-API-Key`. System 헬스/버전/메트릭은 공개.
- 목표 구조: API Gateway 발급 JWT(RBAC/테넌트) + service-to-service JWT — Gateway 전환 시 의존성 교체.
"""

app = FastAPI(
    title="KCloud Monitor API",
    description=API_DESCRIPTION,
    version=APP_VERSION,
    lifespan=lifespan,
)

# ============================================================================
# Middleware (추가 역순 실행: RequestID → RateLimit → Metrics → CORS)
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
# Exception Handlers — design_contracts §6 에러 스키마
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

# 공개: 인증 발급, 시스템 헬스/버전/메트릭
app.include_router(auth.router, prefix=V2, tags=["Authentication"])
app.include_router(system.router, prefix=V2, tags=["System"])

# 보호: 자원/모니터링 전 도메인
app.include_router(clusters.router, prefix=V2, tags=["Clusters"], dependencies=PROTECTED)
app.include_router(nodes.router, prefix=V2, tags=["Nodes & Hardware"], dependencies=PROTECTED)
app.include_router(accelerators.router, prefix=V2, tags=["Accelerators & Partitions"], dependencies=PROTECTED)
app.include_router(storage.router, prefix=V2, tags=["Storage (Ceph)"], dependencies=PROTECTED)
app.include_router(openstack.router, prefix=V2, tags=["OpenStack"], dependencies=PROTECTED)
app.include_router(workloads.router, prefix=V2, tags=["Workloads"], dependencies=PROTECTED)
app.include_router(workloads_global.router, prefix=V2, tags=["Workloads (Global Entry)"], dependencies=PROTECTED)
app.include_router(monitoring.router, prefix=V2, tags=["Monitoring"], dependencies=PROTECTED)
app.include_router(export.router, prefix=V2, tags=["Export"], dependencies=PROTECTED)
app.include_router(resource_map.router, prefix=V2, tags=["Resource-Map"], dependencies=PROTECTED)

# ============================================================================
# Root
# ============================================================================


@app.get("/")
def read_root():
    return {
        "service": "KCloud Monitor API",
        "version": APP_VERSION,
        "api_base": "/api/v2",
        "phase": "scaffold — 라우팅·인증·경로 구조 확정, 전 엔드포인트 스텁",
        "docs": "/docs",
        "authentication": {
            "login": "POST /api/v2/auth/login",
            "alt": "X-API-Key 헤더 (API_KEY 설정 시)",
        },
        "domains": {
            "clusters": "/api/v2/clusters/* (nodes·accelerators·partitions·storage/ceph·openstack·workloads)",
            "workloads_global": "/api/v2/workloads/* (포탈용 전역 진입점)",
            "monitoring": "/api/v2/monitoring/* (전력 P1~P8·시계열·SSE)",
            "export": "/api/v2/export/*",
            "resource_map": "/api/v2/resource-map/* (자원 계보)",
            "system": "/api/v2/system/* (공개)",
        },
        "design_refs": [
            "docs/temp/04-reference/sample_api.md",
            "docs/temp/02-decisions/design_contracts.md",
            "docs/temp/01-domain-plans/openkcloud_storage_ceph_plan.md",
        ],
    }
