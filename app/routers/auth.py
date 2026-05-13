import ipaddress
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

# Symbolic peer labels that count as "this machine" without parsing as IP.
# "testclient" is starlette's TestClient peer; "localhost" is the literal
# hostname some proxies forward as.
_LOCAL_HOSTNAMES = {"localhost", "testclient"}

# Explicit RFC1918 + loopback ranges. We can't use ipaddress.is_private here
# because Python 3.11+ marks RFC5737 documentation blocks (192.0.2/24,
# 198.51.100/24, 203.0.113/24) as private — those are exactly the IPs a
# malicious caller would forge in X-Forwarded-For, so we reject them.
_LOCAL_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),     # loopback
    ipaddress.ip_network("10.0.0.0/8"),      # RFC1918
    ipaddress.ip_network("172.16.0.0/12"),   # RFC1918 + Docker default bridges
    ipaddress.ip_network("192.168.0.0/16"),  # RFC1918
    ipaddress.ip_network("::1/128"),         # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),        # IPv6 unique local
]


def _is_local_hop(value: str) -> bool:
    """A single hop counts as local when it's a loopback or RFC1918 address.

    Production-relevant cases that must pass:
    - 127.0.0.1 / ::1: app-internal healthcheck or loopback curl.
    - 172.16.0.0/12 (Docker bridge), 10.0.0.0/8, 192.168.0.0/16: the
      operator hit the published Docker port from the host or an SSH
      tunnel, so peer.host is the bridge gateway.

    Public addresses (real customers / attackers) and RFC5737
    documentation ranges fall through, so the admin gate rejects them.
    Customer DB-user logins are unaffected — this function is only
    consulted on the legacy admin path."""
    if value in _LOCAL_HOSTNAMES:
        return True
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return False
    return any(addr in net for net in _LOCAL_NETS)


def _is_localhost_request(request: Request) -> bool:
    """True iff the direct peer AND every X-Forwarded-For hop is local
    (loopback or private). A public IP anywhere in the chain fails the
    gate — protecting against spoofed XFF or a misconfigured proxy."""
    direct = request.client.host if request.client else ""
    fwd = request.headers.get("X-Forwarded-For", "")
    hops = [h.strip() for h in fwd.split(",") if h.strip()]
    return all(_is_local_hop(ip) for ip in [direct, *hops])


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
