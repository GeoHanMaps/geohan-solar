import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.models.user import User

ALGORITHM = "HS256"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


def create_access_token(sub: str, user_id: Optional[str] = None) -> str:
    """Create a JWT. `sub` is the principal identifier (email or legacy
    username). `user_id` is the DB UUID for multi-user accounts; legacy
    admin login leaves it None so existing flows keep working."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload: dict = {"sub": sub, "exp": expire}
    if user_id is not None:
        payload["uid"] = user_id
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Geçersiz veya süresi dolmuş token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        raise exc
    if not payload.get("sub"):
        raise exc
    return payload


def get_current_user(token: str = Depends(oauth2_scheme)) -> str:
    """Return the JWT subject — UUID string for DB users, username for
    legacy admin. Routes that only need an authenticated caller use this."""
    return decode_token(token)["sub"]


def get_current_db_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User:
    """Resolve a DB-backed user. Legacy admin tokens (no `uid` claim) are
    rejected — callers that need DB user identity (credits, history) must
    have registered first."""
    payload = decode_token(token)
    uid = payload.get("uid")
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bu işlem için kullanıcı hesabı gereklidir",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        uid_obj = _uuid.UUID(uid)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Geçersiz kullanıcı kimliği",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = session.get(User, uid_obj)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Kullanıcı bulunamadı",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
