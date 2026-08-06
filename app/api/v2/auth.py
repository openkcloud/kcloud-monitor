"""
v2 인증 — 개발/독립 실행용 토큰 발급.

v2 목표 구조(design_contracts §8, system_architecture)에서는 API Gateway가
포탈 JWT(RBAC/테넌트 클레임 포함)와 service-to-service JWT를 발급·검증한다.
Gateway 도입 전까지 본 라우터가 단일 계정 JWT를 발급한다(임시).

보호 라우터 인증은 app.auth.verify_token_or_api_key(JWT Bearer 또는 X-API-Key)로
일원화되어 있어, Gateway 전환 시 이 의존성만 교체하면 된다.
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import Token, create_access_token, verify_credentials, verify_token
from app.config import Settings
from app.deps import get_settings

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    username: str


@router.post("/auth/login", response_model=LoginResponse, summary="로그인(JWT 발급)")
async def login(login_data: LoginRequest, settings: Settings = Depends(get_settings)):
    """계정 검증 후 JWT를 발급한다. Gateway 도입 전 개발용 단일 계정."""
    if (
        login_data.username != settings.API_AUTH_USERNAME
        or login_data.password != settings.API_AUTH_PASSWORD
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    expires = timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    token = create_access_token(
        data={"sub": login_data.username}, expires_delta=expires, settings=settings
    )
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        username=login_data.username,
    )


@router.post("/auth/token", response_model=Token, summary="로그인(HTTP Basic 대체 경로)")
async def login_basic(
    username: str = Depends(verify_credentials),
    settings: Settings = Depends(get_settings),
):
    """HTTP Basic 인증으로 JWT를 발급한다."""
    expires = timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    token = create_access_token(data={"sub": username}, expires_delta=expires, settings=settings)
    return Token(
        access_token=token,
        token_type="bearer",
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.get("/auth/verify", summary="토큰 유효성 확인")
async def verify_current_token(username: str = Depends(verify_token)):
    """Bearer 토큰이 유효한지 확인하고 사용자명을 반환한다."""
    return {"valid": True, "username": username, "message": "Token is valid"}
