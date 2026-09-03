"""토큰 발급과 검증 라우터

보호된 경로의 인증은 app.auth.verify_token_or_api_key 한 곳으로 모여 있음
(JWT Bearer 또는 X-API-Key).
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
    """아이디와 비밀번호로 JWT 발급

    - access_token : 이후 요청의 Authorization: Bearer 헤더에 넣는 값
    - token_type : 항상 "bearer"
    - expires_in : 토큰 유효 시간(초)
    - username : 발급 대상 계정명
    """
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
    """HTTP Basic 인증으로 JWT 발급

    - access_token : 이후 요청의 Authorization: Bearer 헤더에 넣는 값
    - token_type : 항상 "bearer"

    JSON 본문 대신 Basic 인증 헤더를 쓰는 클라이언트용 경로.
    """
    expires = timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    token = create_access_token(data={"sub": username}, expires_delta=expires, settings=settings)
    return Token(
        access_token=token,
        token_type="bearer",
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.get("/auth/verify", summary="토큰 유효성 확인")
async def verify_current_token(username: str = Depends(verify_token)):
    """가지고 있는 토큰이 아직 쓸 수 있는지 확인

    - valid : 토큰 유효 여부
    - username : 토큰에 담긴 계정명
    """
    return {"valid": True, "username": username, "message": "Token is valid"}
