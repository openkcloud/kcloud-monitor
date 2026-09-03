"""서비스 자체 상태 조회 라우터

인증 없이 접근 가능. Kubernetes probe와 배포 파이프라인이 사용.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Response

from app.middleware import get_metrics_content_type, get_metrics_text
from app.services.prometheus import prometheus_client

router = APIRouter()


@router.get("/system/health", summary="헬스체크")
async def get_health(request: Request):
    """이 API 서버가 정상 동작 중인지 확인

    - status : healthy(정상) | degraded(서버는 살아 있으나 데이터소스 미도달)
    - backends.prometheus : connected | unreachable
    - observed_at : 확인 시각
    """
    prometheus_ok = await prometheus_client.ping()
    return {
        # 앱은 살아 있으나 데이터소스 미도달이면 degraded로 강등
        "status": "healthy" if prometheus_ok else "degraded",
        "backends": {
            "prometheus": "connected" if prometheus_ok else "unreachable",
        },
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/system/version", summary="버전 정보")
async def get_version(request: Request):
    """배포된 서비스와 API 버전 확인

    - service : 서비스 이름
    - version : 서비스 버전
    - api_version : API 버전
    """
    return {
        "status": "success",
        "service": "kcloud-monitor",
        "version": request.app.version,
        "api_version": "v2",
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/system/metrics", summary="자체 Prometheus 메트릭")
async def get_self_metrics():
    """이 API 서버가 처리한 요청 수와 응답 지연 메트릭 조회

    - Prometheus가 그대로 수집할 수 있는 텍스트 형식으로 반환
    - 경로별 요청 수, 상태 코드별 요청 수, 응답 지연 분포
    """
    return Response(content=get_metrics_text(), media_type=get_metrics_content_type())
