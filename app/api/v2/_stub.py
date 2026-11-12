"""
아직 구현하지 않은 엔드포인트의 스텁 응답 빌더.

status="not_implemented" + warnings=["NOT_IMPLEMENTED"] 로 스텁임을 명시하고,
호출된 경로와 구현 예정 데이터소스를 본문에 돌려준다.
"""
from datetime import datetime, timezone
from typing import Iterable

from fastapi import Request

DESIGN_DOC = "docs/temp/04-reference/sample_api.md"


def stub(request: Request, description: str, *, sources: Iterable[str] = ()) -> dict:
    route = request.scope.get("route")
    return {
        "status": "not_implemented",
        "api": f"{request.method} {request.url.path}",
        "path_template": getattr(route, "path_format", None),
        "description": description,
        "data_sources": list(sources),
        "design_ref": DESIGN_DOC,
        "data": None,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "warnings": ["NOT_IMPLEMENTED"],
    }
