"""
KCloud Monitor v2 — System (3개, 공개 엔드포인트).

health/version은 스캐폴드 단계에서도 실동작한다(K8s probe·배포 파이프라인용).
metrics는 자체 Prometheus exposition(미들웨어 수집분)을 노출한다.
설계: sample_api.md System 카테고리.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Response

from app.middleware import get_metrics_content_type, get_metrics_text

router = APIRouter()


@router.get("/system/health", summary="헬스체크")
async def get_health(request: Request):
    """서비스 헬스 — 스캐폴드 단계에서는 앱 생존만 보장. 백엔드 연결 상태는 구현 시 채운다."""
    return {
        "status": "healthy",
        "phase": "v2-scaffold",
        "backends": {
            # 구현 시 실제 연결 체크로 대체 (data_source_v1_to_v2.md §2)
            "mimir": "not_configured",
            "postgresql": "not_configured",
            "redis": "not_configured",
            "openstack": "not_configured",
        },
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "is_stale": False,
    }


@router.get("/system/version", summary="버전 정보")
async def get_version(request: Request):
    """서비스/API 버전 — 배포 식별용."""
    return {
        "status": "success",
        "service": "kcloud-monitor",
        "version": request.app.version,
        "api_version": "v2",
        "phase": "scaffold (라우팅·인증·경로 구조만 확정, 전 엔드포인트 스텁)",
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/system/metrics", summary="자체 Prometheus 메트릭")
async def get_self_metrics():
    """이 API 서버 자체의 요청/지연 메트릭(Prometheus exposition, 미들웨어 수집)."""
    return Response(content=get_metrics_text(), media_type=get_metrics_content_type())
