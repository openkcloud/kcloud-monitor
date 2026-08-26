"""
KCloud Monitor v2 — Prometheus HTTP 클라이언트.

데이터소스: Prometheus HTTP API (.env PROMETHEUS_URL 필수).
"""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class PrometheusClient:
    def __init__(self) -> None:
        if not settings.PROMETHEUS_URL:
            raise RuntimeError("PROMETHEUS_URL 환경변수가 설정되지 않았습니다. .env에 PROMETHEUS_URL을 지정하세요.")
        self.base_url: str = settings.PROMETHEUS_URL

    async def ping(self) -> bool:
        """Prometheus 준비 상태 확인 — /-/ready (헬스체크용, 짧은 타임아웃).

        연결 가능하고 200이면 True, 그 외(미도달·비정상)면 False.
        """
        url = f"{self.base_url}/-/ready"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(url)
                return r.status_code == 200
        except httpx.HTTPError as exc:
            logger.warning("Prometheus ping failed: %s — url=%s", exc, url)
            return False

    async def instant(self, query: str) -> list[dict]:
        """즉시 쿼리(instant query).

        GET {base_url}/api/v1/query?query={query}
        성공 시 data.result 목록 반환, 오류 시 빈 리스트 반환.
        """
        url = f"{self.base_url}/api/v1/query"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url, params={"query": query})
                r.raise_for_status()
                return r.json()["data"]["result"]
        except httpx.HTTPStatusError as exc:
            logger.warning("Prometheus instant query HTTP error: %s — query=%r", exc, query)
            return []
        except httpx.RequestError as exc:
            logger.warning("Prometheus instant query request error: %s — query=%r", exc, query)
            return []

    async def range_query(
        self,
        query: str,
        start: str,
        end: str,
        step: str = "60s",
    ) -> list[dict]:
        """범위 쿼리(range query).

        GET {base_url}/api/v1/query_range?query={query}&start={start}&end={end}&step={step}
        성공 시 data.result 목록 반환, 오류 시 빈 리스트 반환.
        """
        url = f"{self.base_url}/api/v1/query_range"
        params = {"query": query, "start": start, "end": end, "step": step}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()["data"]["result"]
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Prometheus range query HTTP error: %s — query=%r start=%s end=%s",
                exc, query, start, end,
            )
            return []
        except httpx.RequestError as exc:
            logger.warning(
                "Prometheus range query request error: %s — query=%r start=%s end=%s",
                exc, query, start, end,
            )
            return []


prometheus_client = PrometheusClient()
