"""
Pytest 공통 픽스처 — v2 스캐폴드.
"""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth import create_access_token
from app.config import Settings
from app.deps import get_settings
from app.main import app


@pytest.fixture
def test_settings():
    return Settings(
        API_AUTH_USERNAME="testuser",
        API_AUTH_PASSWORD="testpass",
        JWT_SECRET_KEY="test-secret-key-for-testing-only",
        JWT_ALGORITHM="HS256",
        JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30,
    )


@pytest.fixture
def client(test_settings):
    app.dependency_overrides[get_settings] = lambda: test_settings
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def auth_token(test_settings):
    return create_access_token(
        data={"sub": test_settings.API_AUTH_USERNAME},
        expires_delta=timedelta(minutes=30),
        settings=test_settings,
    )


@pytest.fixture
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}
