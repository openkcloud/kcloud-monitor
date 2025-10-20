import secrets
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import Settings
from app.deps import get_settings

security = HTTPBasic()

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security), settings: Settings = Depends(get_settings)):
    """Verifies basic authentication credentials."""
    correct_username = secrets.compare_digest(credentials.username, settings.API_AUTH_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, settings.API_AUTH_PASSWORD)
    
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
