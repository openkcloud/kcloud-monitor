"""
KCloud Monitor v2 — System (3개, 공개 엔드포인트).

health/version은 스캐폴드 단계에서도 실동작한다(K8s probe·배포 파이프라인용).
metrics는 자체 Prometheus exposition(미들웨어 수집분)을 노출한다.
설계: sample_api.md System 카테고리.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Response

from app.middleware import get_metrics_content_type, get_metrics_text
from app.services.prometheus import prometheus_client

router = APIRouter()


@router.get("/system/health", summary="헬스체크")
async def get_health(request: Request):
    """서비스 헬스 — 앱 생존 + 유일한 데이터소스(Prometheus) 연결 상태를 실측한다."""
    prometheus_ok = await prometheus_client.ping()
    return {
        # 앱은 살아 있으나 데이터소스 미도달이면 degraded로 강등
        "status": "healthy" if prometheus_ok else "degraded",
        "backends": {
            "prometheus": "connected" if prometheus_ok else "unreachable",
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
        "phase": "partial (clusters·nodes·accelerators·monitoring·workloads 실구현, storage/openstack/resource-map/export 스텁)",
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/system/metrics", summary="자체 Prometheus 메트릭")
async def get_self_metrics():
    """이 API 서버 자체의 요청/지연 메트릭(Prometheus exposition, 미들웨어 수집)."""
    return Response(content=get_metrics_text(), media_type=get_metrics_content_type())
