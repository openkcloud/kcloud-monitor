"""
v2 스캐폴드 공통 스텁 응답 빌더.

모든 v2 엔드포인트는 실제 구현 전까지 stub()으로 응답한다(HTTP 200).
포탈/클라이언트가 경로·파라미터·인증을 미리 연동할 수 있도록,
호출된 경로와 구현 예정 데이터소스를 응답 본문에 그대로 돌려준다.

응답 형태는 design_contracts §6 공통 응답 정책(observed_at, is_stale, warnings)을
따르되, 스텁임을 status="not_implemented" + warnings=["NOT_IMPLEMENTED"]로 명시한다.
실제 구현 시: 핸들러 본문을 crud/서비스 호출로 교체하고 이 모듈 의존을 제거한다.
"""
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from fastapi import Request
from fastapi.responses import StreamingResponse

DESIGN_DOC = "docs/temp/04-reference/sample_api.md"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stub(
    request: Request,
    description: str,
    *,
    sources: Iterable[str] = (),
    ref: Optional[str] = None,
    example: Any = None,
    links: Optional[dict] = None,
    params: Optional[dict] = None,
) -> dict:
    """스텁 응답 본문 생성.

    Args:
        request: 현재 요청 (메서드/경로/경로템플릿 echo용)
        description: 이 API의 정의(한국어 한 줄)
        sources: 구현 시 사용할 데이터소스 목록 (예: "Mimir(PromQL: DCGM_*)")
        ref: 설계 문서 참조 (기본: sample_api.md)
        example: 임시 예시 페이로드(응답 형태 예고) — 없으면 None
        links: _links (전역 진입점/별칭 경로는 self + canonical 필수, design_contracts §6)
        params: 수신한 쿼리 파라미터 echo (연동 테스트용)
    """
    route = request.scope.get("route")
    body: dict = {
        "status": "not_implemented",
        "api": f"{request.method} {request.url.path}",
        "path_template": getattr(route, "path_format", None),
        "description": description,
        "data_sources": list(sources),
        "design_ref": ref or DESIGN_DOC,
        "data": example,
        "observed_at": _now(),
        "is_stale": False,
        "warnings": ["NOT_IMPLEMENTED"],
    }
    if params:
        body["params"] = params
    if links:
        body["_links"] = links
    return body


def sse_stub(
    request: Request,
    description: str,
    *,
    sources: Iterable[str] = (),
    ref: Optional[str] = None,
) -> StreamingResponse:
    """SSE 엔드포인트용 스텁: heartbeat 1회 + 스텁 이벤트 1회 송신 후 종료.

    실제 구현 계약(design_contracts §7): text/event-stream, 15초 heartbeat,
    Last-Event-ID 재개 지원. 스텁 단계에서는 이벤트 형태만 예고한다.
    """
    envelope = stub(request, description, sources=sources, ref=ref)

    async def gen():
        yield f"event: heartbeat\ndata: {json.dumps({'observed_at': _now()})}\n\n"
        yield f"event: stub\ndata: {json.dumps(envelope, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
