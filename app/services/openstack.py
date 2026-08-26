"""
KCloud Monitor v2 — OpenStack (Keystone/Nova) 클라이언트.

용도: 물리 하이퍼바이저 ↔ VM 배치 ↔ 가속기(flavor PCI passthrough) 매핑 조회.
데이터소스: Keystone v3(인증), Nova compute API.

설계 주의 (docs/API_RESTRUCTURE_PLAN.md §4.3):
  - 생성자에서 절대 raise 하지 않는다. 크리덴셜 없으면 configured=False 로 두고
    앱은 정상 기동한다. 미설정/실패 시 호출부는 NOT_CONFIGURED / UPSTREAM_ERROR 로 처리.
  - Keystone catalog의 nova/placement URL은 내부 DNS(*.svc.cluster.local)라 외부에서
    도달 불가하므로 무시하고, 설정된 OPENSTACK_NOVA_URL(endpoint_override)을 사용한다.
  - Nova 조회는 microversion 2.88 헤더로 OS-EXT-SRV-ATTR:host 등을 얻는다.
"""
import logging
import time
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Nova microversion — OS-EXT-SRV-ATTR:host, flavor.extra_specs 노출에 필요
NOVA_MICROVERSION = "2.88"


class OpenStackError(Exception):
    """OpenStack 업스트림 호출 실패 (미설정과 구분)."""


class OpenStackClient:
    """Keystone 토큰 발급 + Nova 조회. 토큰은 만료 전까지 캐시한다."""

    def __init__(self) -> None:
        self.auth_url: Optional[str] = settings.OPENSTACK_AUTH_URL
        self.username: Optional[str] = settings.OPENSTACK_USERNAME
        self.password: Optional[str] = settings.OPENSTACK_PASSWORD
        self.project: Optional[str] = settings.OPENSTACK_PROJECT_NAME
        self.user_domain: str = settings.OPENSTACK_USER_DOMAIN or "Default"
        self.project_domain: str = settings.OPENSTACK_PROJECT_DOMAIN or "Default"
        # catalog 우회용 명시 URL (없으면 auth_url 호스트에서 유추하지 않고 그냥 None → nova 호출 불가 처리)
        self.nova_url: Optional[str] = (
            settings.OPENSTACK_NOVA_URL.rstrip("/") if settings.OPENSTACK_NOVA_URL else None
        )
        self.placement_url: Optional[str] = (
            settings.OPENSTACK_PLACEMENT_URL.rstrip("/") if settings.OPENSTACK_PLACEMENT_URL else None
        )
        self.magnum_url: Optional[str] = (
            settings.OPENSTACK_MAGNUM_URL.rstrip("/") if settings.OPENSTACK_MAGNUM_URL else None
        )
        self._token: Optional[str] = None
        self._token_exp: float = 0.0  # monotonic 만료 시각

    @property
    def configured(self) -> bool:
        """인증에 필요한 최소 크리덴셜이 모두 있는가."""
        return bool(self.auth_url and self.username and self.password and self.project)

    @property
    def nova_configured(self) -> bool:
        return self.configured and bool(self.nova_url)

    @property
    def magnum_configured(self) -> bool:
        return self.configured and bool(self.magnum_url)

    async def _get_token(self) -> str:
        """Keystone v3 password 인증으로 토큰 발급(캐시). 실패 시 OpenStackError."""
        # 캐시된 토큰이 아직 유효하면 재사용 (만료 5분 전 갱신)
        if self._token and time.monotonic() < self._token_exp - 300:
            return self._token
        if not self.configured:
            raise OpenStackError("NOT_CONFIGURED")

        body = {
            "auth": {
                "identity": {
                    "methods": ["password"],
                    "password": {
                        "user": {
                            "name": self.username,
                            "domain": {"name": self.user_domain},
                            "password": self.password,
                        }
                    },
                },
                "scope": {
                    "project": {
                        "name": self.project,
                        "domain": {"name": self.project_domain},
                    }
                },
            }
        }
        url = f"{self.auth_url.rstrip('/')}/auth/tokens"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(url, json=body)
                r.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Keystone 인증 실패: %s — url=%s", exc, url)
            raise OpenStackError("UPSTREAM_ERROR") from exc

        token = r.headers.get("X-Subject-Token")
        if not token:
            raise OpenStackError("UPSTREAM_ERROR")
        self._token = token
        # 토큰 유효시간 파싱 실패해도 최소 30분은 캐시
        self._token_exp = time.monotonic() + 1800
        return token

    async def _nova_get(self, path: str, microversion: Optional[str] = None) -> dict:
        """Nova GET (catalog 무시, endpoint_override + microversion). 실패 시 OpenStackError.

        microversion 미지정 시 기본(2.88). 하이퍼바이저 용량 필드(vcpus/memory_mb/
        running_vms 등)는 2.88에서 삭제되어 placement로 이관됐으므로, 하이퍼바이저
        조회는 2.53으로 낮춰 호출해야 한다.
        """
        if not self.nova_configured:
            raise OpenStackError("NOT_CONFIGURED")
        token = await self._get_token()
        url = f"{self.nova_url}{path}"
        headers = {
            "X-Auth-Token": token,
            "OpenStack-API-Version": f"compute {microversion or NOVA_MICROVERSION}",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(url, headers=headers)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError as exc:
            logger.warning("Nova 호출 실패: %s — url=%s", exc, url)
            raise OpenStackError("UPSTREAM_ERROR") from exc

    async def _keystone_get(self, path: str) -> dict:
        """Keystone GET (auth_url 기준, 토큰 인증). 실패 시 OpenStackError."""
        if not self.configured:
            raise OpenStackError("NOT_CONFIGURED")
        token = await self._get_token()
        url = f"{self.auth_url.rstrip('/')}{path}"
        headers = {"X-Auth-Token": token}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(url, headers=headers)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError as exc:
            logger.warning("Keystone 호출 실패: %s — url=%s", exc, url)
            raise OpenStackError("UPSTREAM_ERROR") from exc

    async def _magnum_get(self, path: str) -> dict:
        """Magnum GET (endpoint_override + container-infra microversion). 실패 시 OpenStackError."""
        if not self.magnum_configured:
            raise OpenStackError("NOT_CONFIGURED")
        token = await self._get_token()
        url = f"{self.magnum_url}{path}"
        headers = {
            "X-Auth-Token": token,
            "OpenStack-API-Version": "container-infra latest",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(url, headers=headers)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError as exc:
            logger.warning("Magnum 호출 실패: %s — url=%s", exc, url)
            raise OpenStackError("UPSTREAM_ERROR") from exc

    # ── 조회 메서드 ────────────────────────────────────────────────────────

    async def hypervisors(self) -> list[dict]:
        """물리 하이퍼바이저 목록 (os-hypervisors/detail).

        2.53으로 호출 — 2.88+에서는 vcpus/memory_mb/local_gb/running_vms 등
        용량·사용량 필드가 응답에서 제거되기 때문(placement로 이관됨).
        """
        data = await self._nova_get("/os-hypervisors/detail", microversion="2.53")
        return data.get("hypervisors", [])

    async def servers(self) -> list[dict]:
        """모든 프로젝트의 VM 목록 (servers/detail, all_tenants). OS-EXT-SRV-ATTR:host 포함."""
        data = await self._nova_get("/servers/detail?all_tenants=1")
        return data.get("servers", [])

    async def flavors(self) -> dict[str, dict]:
        """flavor 이름 → extra_specs 매핑 (pci_passthrough:alias 로 가속기 종류·개수 판별)."""
        data = await self._nova_get("/flavors/detail")
        out: dict[str, dict] = {}
        for f in data.get("flavors", []):
            name = f.get("name")
            if name:
                out[name] = f.get("extra_specs", {}) or {}
        return out

    async def keystone_projects(self) -> dict[str, str]:
        """프로젝트 uuid → 이름 매핑 (Keystone GET /projects)."""
        data = await self._keystone_get("/projects")
        out: dict[str, str] = {}
        for p in data.get("projects", []):
            pid = p.get("id")
            name = p.get("name")
            if pid and name:
                out[pid] = name
        return out

    async def magnum_clusters(self) -> list[dict]:
        """Magnum K8s 클러스터 목록 (GET /clusters, project_id 없으면 detail 보강).

        각 항목: name / uuid / project_id / node_count / master_count / status.
        """
        data = await self._magnum_get("/clusters")
        clusters = data.get("clusters", [])
        out: list[dict] = []
        for c in clusters:
            uuid = c.get("uuid")
            entry = {
                "name": c.get("name"),
                "uuid": uuid,
                "project_id": c.get("project_id"),
                "node_count": c.get("node_count"),
                "master_count": c.get("master_count"),
                "status": c.get("status"),
            }
            # 목록 응답에 project_id가 없으면 상세로 보강 (Magnum 버전차 대응)
            if not entry["project_id"] and uuid:
                try:
                    detail = await self._magnum_get(f"/clusters/{uuid}")
                    entry["project_id"] = detail.get("project_id")
                    entry["node_count"] = entry["node_count"] or detail.get("node_count")
                    entry["master_count"] = entry["master_count"] or detail.get("master_count")
                    entry["status"] = entry["status"] or detail.get("status")
                except OpenStackError:
                    pass
            out.append(entry)
        return out

    async def accelerator_vm_projects(self) -> dict[str, str]:
        """가속기 passthrough VM의 alias(소문자) → 프로젝트명 (Nova+Keystone).

        Magnum이 모르는 단독 가속기 VM(예: L40S·Rebellions)의 프로젝트를 채우는 폴백용.
        예: {"l40s": "admin", "rebellions": "admin", "furiosa-rngd": "admin"}
        """
        flavor_specs = await self.flavors()
        servers = await self.servers()
        try:
            projects = await self.keystone_projects()
        except OpenStackError:
            projects = {}
        out: dict[str, str] = {}
        for s in servers:
            flavor_name = (s.get("flavor") or {}).get("original_name") or (s.get("flavor") or {}).get("id")
            specs = flavor_specs.get(flavor_name) if flavor_name else None
            if not specs:
                continue
            parsed = parse_accelerator_alias(specs)
            if not parsed:
                continue
            pid = s.get("tenant_id")
            pname = projects.get(pid, pid)
            if pname:
                out[parsed[0].lower()] = pname
        return out

    async def cluster_project_map(self) -> dict[str, dict]:
        """서비스 K8s 클러스터 이름 → {project_id, project_name, node_count, status}.

        Magnum 클러스터명(= Prometheus cluster 라벨)을 키로 한다.
        project_name은 Keystone에서 uuid→이름 변환(권한 없으면 uuid 유지).
        """
        clusters = await self.magnum_clusters()
        try:
            projects = await self.keystone_projects()
        except OpenStackError:
            projects = {}
        out: dict[str, dict] = {}
        for c in clusters:
            name = c.get("name")
            if not name:
                continue
            pid = c.get("project_id")
            out[name] = {
                "project_id": pid,
                "project_name": projects.get(pid, pid),
                "node_count": c.get("node_count"),
                "status": c.get("status"),
            }
        return out


# ---------------------------------------------------------------------------
# flavor extra_specs → 가속기 파싱 헬퍼
# ---------------------------------------------------------------------------

def parse_accelerator_alias(extra_specs: dict) -> Optional[tuple[str, int]]:
    """flavor extra_specs의 pci_passthrough:alias → (alias, count).

    예: {"pci_passthrough:alias": "L40S:1"} → ("L40S", 1)
        {"pci_passthrough:alias": "furiosa-rngd:1"} → ("furiosa-rngd", 1)
    가속기 없는 flavor면 None.
    """
    raw = extra_specs.get("pci_passthrough:alias")
    if not raw:
        return None
    # 형식: "alias:count" (여러 개는 콤마 구분 가능하지만 현 환경은 단일)
    first = raw.split(",")[0].strip()
    if ":" not in first:
        return (first, 1)
    alias, _, count = first.rpartition(":")
    try:
        return (alias, int(count))
    except ValueError:
        return (alias, 1)


openstack_client = OpenStackClient()
