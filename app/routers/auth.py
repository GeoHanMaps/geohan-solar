import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.auth import create_access_token
from app.config import settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/token", summary="API token al")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    OAuth2 password flow: username + password → Bearer JWT.
    Token süresi: `ACCESS_TOKEN_EXPIRE_MINUTES` (varsayılan 24 saat).
    """
    valid_user = secrets.compare_digest(form_data.username, settings.api_username)
    valid_pass = secrets.compare_digest(form_data.password, settings.api_password)
    if not (valid_user and valid_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Kullanıcı adı veya şifre yanlış",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(sub=form_data.username)
    return {"access_token": token, "token_type": "bearer"}
