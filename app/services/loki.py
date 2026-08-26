"""
KCloud Monitor v2 — Loki HTTP 클라이언트.

데이터소스: Loki HTTP API (.env LOKI_URL).
PrometheusClient와 동형 구조 — ping/query_range/labels/label_values/volume.
"""
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_EMPTY_STREAMS = {"status": "success", "data": {"resultType": "streams", "result": []}}


class LokiClient:
    def __init__(self) -> None:
        self.base_url: str = settings.LOKI_URL or ""

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    async def ping(self) -> bool:
        if not self.configured:
            return False
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{self.base_url}/ready")
                return r.status_code == 200
        except httpx.HTTPError as exc:
            logger.warning("Loki ping failed: %s — url=%s", exc, self.base_url)
            return False

    async def query_range(
        self,
        query: str,
        start: str,
        end: str,
        limit: int = 100,
        direction: str = "backward",
    ) -> dict:
        if not self.configured:
            return _EMPTY_STREAMS
        url = f"{self.base_url}/loki/api/v1/query_range"
        params = {
            "query": query,
            "start": start,
            "end": end,
            "limit": str(limit),
            "direction": direction,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("Loki query_range HTTP error: %s — query=%r", exc, query)
            return _EMPTY_STREAMS
        except httpx.RequestError as exc:
            logger.warning("Loki query_range request error: %s — query=%r", exc, query)
            return _EMPTY_STREAMS

    async def labels(self, start: Optional[str] = None, end: Optional[str] = None) -> list[str]:
        if not self.configured:
            return []
        url = f"{self.base_url}/loki/api/v1/labels"
        params: dict[str, str] = {}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json().get("data", [])
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning("Loki labels error: %s", exc)
            return []

    async def label_values(
        self, label_name: str, start: Optional[str] = None, end: Optional[str] = None,
    ) -> list[str]:
        if not self.configured:
            return []
        url = f"{self.base_url}/loki/api/v1/label/{label_name}/values"
        params: dict[str, str] = {}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json().get("data", [])
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning("Loki label_values error: %s — label=%s", exc, label_name)
            return []

    async def volume(
        self, query: str, start: str, end: str, limit: int = 100,
    ) -> dict:
        """Loki index/volume (Loki 2.9+). 미지원 시 빈 dict."""
        if not self.configured:
            return {}
        url = f"{self.base_url}/loki/api/v1/index/volume"
        params = {"query": query, "start": start, "end": end, "limit": str(limit)}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning("Loki volume error: %s", exc)
            return {}


loki_client = LokiClient()
