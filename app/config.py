"""
KCloud Monitor v2 설정 (.env 또는 환경변수).
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

    # ── v2 데이터소스 ─────────────────────────────────────────────────────
    PROMETHEUS_URL: Optional[str] = Field("http://localhost:9090", description="Prometheus HTTP API URL",)
    LOKI_URL: Optional[str] = Field(None, description="Loki HTTP API URL")
    DATABASE_URL: Optional[str] = Field(
        None, description="PostgreSQL — 알람 저장소(정책/채널/활성 알람/이력), resource-map 원장(예정)"
    )
    # ── 알람 ──────────────────────────────────────────────────────────────
    ALERT_EVAL_INTERVAL_SEC: int = Field(30, ge=5, description="알람 정책 평가 주기(초)")
    ALERT_REPEAT_INTERVAL_MIN: int = Field(240, ge=1, description="firing 유지 중 재알림 간격(분)")
    ALERT_TARGET_GONE_MIN: int = Field(
        60, ge=5, description="평가 결과에서 이 시간(분) 넘게 안 보인 대상의 알람은 대상 소멸로 보고 해제"
    )
    SMTP_HOST: Optional[str] = Field(None, description="알람 메일 SMTP 호스트")
    SMTP_PORT: int = Field(587, description="SMTP 포트")
    SMTP_USER: Optional[str] = Field(None, description="SMTP 계정")
    SMTP_PASSWORD: Optional[str] = Field(None, description="SMTP 비밀번호")
    SMTP_FROM: Optional[str] = Field(None, description="알람 메일 발신 주소")
    OPENSTACK_AUTH_URL: Optional[str] = Field(
        None, description="Keystone auth URL (예: http://host:5000/v3)"
    )
    OPENSTACK_USERNAME: Optional[str] = Field(None, description="OpenStack 계정(admin/system-reader 필요)")
    OPENSTACK_PASSWORD: Optional[str] = Field(None, description="OpenStack 비밀번호")
    OPENSTACK_PROJECT_NAME: Optional[str] = Field(None, description="OpenStack 프로젝트")
    OPENSTACK_USER_DOMAIN: str = Field("Default", description="Keystone 사용자 도메인")
    OPENSTACK_PROJECT_DOMAIN: str = Field("Default", description="Keystone 프로젝트 도메인")
    OPENSTACK_NOVA_URL: Optional[str] = Field(None, description="Nova compute API URL 오버라이드")
    OPENSTACK_MAGNUM_URL: Optional[str] = Field(None, description="Magnum(container-infra) API URL 오버라이드")
    OPENSTACK_CYBORG_URL: Optional[str] = Field(
        None, description="Cyborg(accelerator) API URL 오버라이드 (예: http://host:6666/accelerator/v2)"
    )


settings = Settings()
