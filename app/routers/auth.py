import secrets
from typing import Iterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import create_access_token, get_current_db_user
from app.config import settings
from app.db import get_engine, get_session
from app.models.credit_transaction import CreditTransaction, REASON_SIGNUP_BONUS
from app.models.user import SIGNUP_BONUS_CREDITS, User
from app.schemas import RegisterRequest, TokenResponse, UserResponse
from app.security import hash_password, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# IPs that count as "this machine" for the admin localhost gate. "testclient"
# is starlette's TestClient peer label — never appears in real traffic.
_LOCALHOST_IPS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _is_localhost_request(request: Request) -> bool:
    """True iff the request's direct peer AND every hop in X-Forwarded-For
    are localhost. nginx behind us terminates external clients with a
    non-local X-Forwarded-For entry, so this gate catches them even when the
    direct peer (nginx itself) is 127.0.0.1."""
    direct = request.client.host if request.client else ""
    fwd = request.headers.get("X-Forwarded-For", "")
    hops = [h.strip() for h in fwd.split(",") if h.strip()]
    return all(ip in _LOCALHOST_IPS for ip in [direct, *hops])


def session_or_none() -> Iterator[Optional[Session]]:
    """Yield a DB session, or None if DATABASE_URL isn't configured.

    /token uses this so deployments without the DB layer can still log in as
    the legacy env-based admin. Tests override this dep when they bring up an
    in-memory DB."""
    try:
        get_engine()
    except RuntimeError:
        yield None
        return
    from app.db import _SessionLocal  # populated by get_engine()

    assert _SessionLocal is not None
    with _SessionLocal() as session:
        yield session


@router.post("/register", response_model=TokenResponse, status_code=201,
             summary="Yeni kullanıcı kaydı")
def register(req: RegisterRequest, session: Session = Depends(get_session)):
    """Email/şifre ile kayıt. Hesap oluşur, signup bonus kredisi yüklenir,
    JWT döner (otomatik login)."""
    email = req.email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=422, detail="Geçersiz e-posta adresi")

    user = User(
        email=email,
        password_hash=hash_password(req.password),
        credits=SIGNUP_BONUS_CREDITS,
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Bu e-posta zaten kayıtlı")

    session.add(CreditTransaction(
        user_id=user.id,
        amount=SIGNUP_BONUS_CREDITS,
        balance_after=user.credits,
        reason=REASON_SIGNUP_BONUS,
    ))
    session.commit()
    session.refresh(user)

    token = create_access_token(sub=str(user.id), user_id=str(user.id))
    return TokenResponse(access_token=token)


@router.post("/token", response_model=TokenResponse, summary="API token al")
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Optional[Session] = Depends(session_or_none),
):
    """OAuth2 password flow. DB'de kayıtlı kullanıcılar email/şifre ile;
    legacy admin env-based kimlik bilgileriyle giriş yapar. Admin login
    `ADMIN_LOGIN_REQUIRE_LOCALHOST=true` (default) iken yalnızca yerelden
    kabul edilir — sunucuya SSH tunnel ile bağlanın."""
    if session is not None:
        email = form_data.username.strip().lower()
        user = session.query(User).filter(User.email == email).one_or_none()
        if user and verify_password(form_data.password, user.password_hash):
            token = create_access_token(sub=str(user.id), user_id=str(user.id))
            return TokenResponse(access_token=token)

    valid_user = secrets.compare_digest(form_data.username, settings.api_username)
    valid_pass = secrets.compare_digest(form_data.password, settings.api_password)
    if valid_user and valid_pass:
        if settings.admin_login_require_localhost and not _is_localhost_request(request):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Admin login sadece localhost'tan kabul edilir",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return TokenResponse(access_token=create_access_token(sub=form_data.username))

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Kullanıcı adı veya şifre yanlış",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.get("/me", response_model=UserResponse, summary="Aktif kullanıcı")
def me(user: User = Depends(get_current_db_user)):
    return UserResponse(id=str(user.id), email=user.email, credits=user.credits)
