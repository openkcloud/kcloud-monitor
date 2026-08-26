"""
KCloud Monitor v2 설정.

스캐폴드 단계에서 실제로 사용되는 키는 인증/운영 그룹뿐이다.
"v2 데이터소스" 그룹은 구현 시 사용할 자리(placeholder)로,
data_source_v1_to_v2.md §4(v1→v2 config 전환표)를 따른다.
"""
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── 인증 (API Gateway 도입 전 개발용 단일 계정 + API Key) ─────────────
    API_AUTH_USERNAME: str = Field("admin", description="API username")
    API_AUTH_PASSWORD: str = Field("changeme", description="API password")
    JWT_SECRET_KEY: str = Field("change-this-secret-key", description="JWT signing secret")
    JWT_ALGORITHM: str = Field("HS256", description="JWT algorithm")
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(60, description="JWT expiration (minutes)")
    API_KEY: Optional[str] = Field(
        None, description="X-API-Key 병행 인증 키. 미설정 시 JWT만 허용"
    )

    # ── 운영 ──────────────────────────────────────────────────────────────
    CORS_ALLOW_ORIGINS: str = Field("*", description="허용 오리진(콤마 구분) 또는 *")
    RATE_LIMIT_ENABLED: bool = Field(False, description="레이트리밋 활성화")
    RATE_LIMIT_PER_MINUTE: int = Field(120, ge=1, description="클라이언트당 분당 요청 수")
    LOG_LEVEL: str = Field("INFO", description="로그 레벨")

    # ── v2 데이터소스 (스캐폴드 단계 미사용 — 구현 시 활성화) ─────────────
    PROMETHEUS_URL: Optional[str] = Field("http://localhost:9090", description="Prometheus HTTP API URL",)
    LOKI_URL: Optional[str] = Field(None, description="Loki HTTP API URL")
    TEMPO_URL: Optional[str] = Field(None, description="Tempo HTTP API URL")
    DATABASE_URL: Optional[str] = Field(
        None, description="PostgreSQL — resource-map 원장"
    )
    REDIS_URL: Optional[str] = Field(
        None, description="Redis — 캐시 + Streams 이벤트 버스 (예: redis://host:6379/0)"
    )
    OPENSTACK_AUTH_URL: Optional[str] = Field(
        None, description="Keystone auth URL (예: http://host:5000/v3)"
    )
    OPENSTACK_USERNAME: Optional[str] = Field(None, description="OpenStack 계정(admin/system-reader 필요)")
    OPENSTACK_PASSWORD: Optional[str] = Field(None, description="OpenStack 비밀번호")
    OPENSTACK_PROJECT_NAME: Optional[str] = Field(None, description="OpenStack 프로젝트")
    OPENSTACK_USER_DOMAIN: str = Field("Default", description="Keystone 사용자 도메인")
    OPENSTACK_PROJECT_DOMAIN: str = Field("Default", description="Keystone 프로젝트 도메인")
    OPENSTACK_NOVA_URL: Optional[str] = Field(None, description="Nova compute API URL 오버라이드")
    OPENSTACK_PLACEMENT_URL: Optional[str] = Field(None, description="Placement API URL 오버라이드")
    OPENSTACK_MAGNUM_URL: Optional[str] = Field(None, description="Magnum(container-infra) API URL 오버라이드")
    CLUSTER_REGISTRY: Optional[str] = Field(
        None,
        description="클러스터 레지스트리 JSON — 관리/서비스 구분·접속 정보. "
        '예: [{"name":"mgmt","type":"management"},{"name":"svc-1","type":"service"}]',
    )


settings = Settings()
