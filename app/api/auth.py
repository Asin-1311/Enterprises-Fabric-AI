"""Sign-in for the API (author's request, 2026-11-14). Routes under /api/v1/auth.

How it works:
- POST /auth/login checks the email and password (scrypt hash) and returns a random
  session token. The browser sends it back as `Authorization: Bearer <token>`.
- `current_user` (a FastAPI dependency) turns that header into the signed-in User. Every
  protected endpoint uses it, so the user is never taken from the request body.
- POST /auth/register creates a STAFF account with NO engagements. Nobody can make
  themselves a manager or partner, or join a client team; roles and teams stay in
  data/users.csv and data/engagements.csv. Registration is OFF unless
  ALLOW_SELF_REGISTRATION=true, and then only firm-domain addresses are accepted (M9).
- Every /auth/* route is rate-limited per IP, and 10 failed sign-ins in 15 minutes lock
  the IP out for a while (app/api/ratelimit.py, M9). Both give HTTP 429 + Retry-After.
- POST /auth/forgot-password always gives the same answer, so it does not reveal which
  emails have accounts. If the account exists, a one-time link valid for a few minutes
  is written to the API console (there is no email server in this project).
- POST /auth/reset-password sets the new password, uses up the link and signs the user
  out everywhere.
Tokens are stored only as SHA-256 digests. Every action is written to audit_log by user
id; emails and passwords are never logged.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import (
    AuthConfigOut,
    ForgotPasswordRequest,
    MessageOut,
    PasswordResetOut,
    SignedOutOut,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SessionOut,
    UserOut,
)
from app.db.models import AuthSession, Engagement, EngagementMember, PasswordReset, User
from app.api.ratelimit import FailureLockout, TokenBucket, retry_after_header
from app.db.session import write_audit_log
from app.security.passwords import (
    hash_password,
    new_token,
    password_problem,
    token_digest,
    verify_password,
)

log = logging.getLogger("uvicorn.error")  # shown in the API window

SIGN_IN_FAILED = "Incorrect email or password."
RESET_SENT = "If an account exists for that email, a password reset link has been sent."
RESET_INVALID = "This reset link is invalid or has expired. Request a new one."
REGISTRATION_CLOSED = "Accounts are created by the firm administrator."
TOO_MANY = "Too many requests. Please wait a moment and try again."
LOCKED_OUT = "Too many failed sign-in attempts. Please try again later."

router = APIRouter(prefix="/api/v1/auth", tags=["sign-in"])
_bearer = HTTPBearer(auto_error=False)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _limits(request: Request) -> tuple[TokenBucket, FailureLockout]:
    """The app's sign-in limiter, created on first use from the settings."""
    state = request.app.state
    if getattr(state, "auth_limits", None) is None:
        s = state.services.settings
        state.auth_limits = (TokenBucket(s.auth_rate_per_minute),
                             FailureLockout(s.auth_lockout_failures, s.auth_lockout_minutes))
    return state.auth_limits


def guard_auth_route(request: Request) -> None:
    """Dependency on every /auth/* route that takes credentials or sends links."""
    bucket, lockout = _limits(request)
    ip = _client_ip(request)
    wait = lockout.locked_for(ip)
    if wait:
        raise HTTPException(429, LOCKED_OUT, headers=retry_after_header(wait))
    wait = bucket.take(ip)
    if wait:
        raise HTTPException(429, TOO_MANY, headers=retry_after_header(wait))


def utc_now() -> datetime:
    """Current UTC time without tzinfo (SQLite stores naive datetimes)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sessions(request: Request):
    return request.app.state.services.sessions


def user_out(s: Session, user: User) -> UserOut:
    """Public view of a user: role, email and the client names of their engagements."""
    if user.role == "partner":
        engagements = ["all engagements"]
    else:
        engagements = sorted(s.scalars(
            select(Engagement.client_name)
            .join(EngagementMember, EngagementMember.engagement_id == Engagement.engagement_id)
            .where(EngagementMember.user_id == user.user_id)))
    return UserOut(user_id=user.user_id, name=user.name, role=user.role, email=user.email,
                   is_admin=bool(user.is_admin),
                   engagements=engagements)


def current_user(request: Request,
                 creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> User:
    """The signed-in user, from the Bearer token. 401 if missing, unknown or expired."""
    if creds is None:
        raise HTTPException(401, "Sign in required.", headers={"WWW-Authenticate": "Bearer"})
    with _sessions(request)() as s:
        row = s.get(AuthSession, token_digest(creds.credentials))
        user = s.get(User, row.user_id) if row and row.expires_at > utc_now() else None
    if user is None:
        raise HTTPException(401, "Your session has expired. Sign in again.",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


def _start_session(s: Session, user: User, hours: float) -> SessionOut:
    """Create a session row (digest only) and return the token to the client once."""
    now = utc_now()
    s.execute(delete(AuthSession).where(AuthSession.user_id == user.user_id,
                                        AuthSession.expires_at <= now))  # tidy expired ones
    token = new_token()
    expires = now + timedelta(hours=hours)
    s.add(AuthSession(token_digest=token_digest(token), user_id=user.user_id,
                      created_at=now, expires_at=expires))
    s.commit()
    return SessionOut(token=token, expires_at=expires.replace(tzinfo=timezone.utc),
                      user=user_out(s, user))


def _next_user_id(s: Session) -> str:
    """U0001, U0002, ... for self-registered accounts (demo users keep S01, M01, P01)."""
    numbers = [int(uid[1:]) for uid in s.scalars(select(User.user_id))
               if re.fullmatch(r"U\d{4}", uid)]
    return f"U{max(numbers, default=0) + 1:04d}"


# --------------------------------------------------------------------------- routes

@router.get("/config", response_model=AuthConfigOut)
def auth_config(request: Request) -> dict:
    """What the sign-in screen may offer (public: no secrets, no user data)."""
    return {"self_registration": request.app.state.services.settings.allow_self_registration}


@router.post("/login", response_model=SessionOut, dependencies=[Depends(guard_auth_route)])
def login(req: LoginRequest, request: Request) -> SessionOut:
    """Check email and password; on success return a session token."""
    settings = request.app.state.services.settings
    with _sessions(request)() as s:
        user = s.scalars(select(User).where(User.email == req.email)).first()
        if not verify_password(req.password, user.password_hash if user else None):
            actor = user.user_id if user else "unknown"
            write_audit_log(s, actor, "sign_in_failed", None)
            _, lockout = _limits(request)
            lockout.record_failure(_client_ip(request))
            if lockout.locked_for(_client_ip(request)):
                write_audit_log(s, actor, "sign_in_locked_out", None)
            raise HTTPException(401, SIGN_IN_FAILED)
        write_audit_log(s, user.user_id, "signed_in", user.user_id)
        return _start_session(s, user, settings.session_hours)


@router.post("/register", response_model=SessionOut, status_code=201,
             dependencies=[Depends(guard_auth_route)])
def register(req: RegisterRequest, request: Request) -> SessionOut:
    """Create a staff account with no engagements, and sign it in. Off by default."""
    settings = request.app.state.services.settings
    if not settings.allow_self_registration:
        raise HTTPException(403, REGISTRATION_CLOSED)
    if not req.email.endswith("@" + settings.self_registration_domain.lower()):
        raise HTTPException(422, f"Use your firm email address (@{settings.self_registration_domain}).")
    problem = password_problem(req.password)
    if problem:
        raise HTTPException(422, problem)
    with _sessions(request)() as s:
        if s.scalars(select(User).where(User.email == req.email)).first():
            raise HTTPException(409, "An account with this email already exists. Sign in instead.")
        user = User(user_id=_next_user_id(s), name=req.name, role="staff", synthetic=False,
                    email=req.email, password_hash=hash_password(req.password))
        s.add(user)
        try:
            s.commit()
        except IntegrityError as exc:  # two registrations for one email at the same moment
            s.rollback()
            raise HTTPException(409, "An account with this email already exists.") from exc
        write_audit_log(s, user.user_id, "account_created", f"{user.user_id} role=staff")
        return _start_session(s, user, settings.session_hours)


@router.get("/me", response_model=UserOut)
def me(request: Request, user: User = Depends(current_user)) -> UserOut:
    """The signed-in user (used by the UI to restore a session)."""
    with _sessions(request)() as s:
        return user_out(s, user)


@router.post("/logout", response_model=SignedOutOut)
def logout(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
           user: User = Depends(current_user)) -> dict:
    """End this session (the token stops working immediately)."""
    with _sessions(request)() as s:
        s.execute(delete(AuthSession).where(AuthSession.token_digest == token_digest(creds.credentials)))
        s.commit()
        write_audit_log(s, user.user_id, "signed_out", user.user_id)
    return {"signed_out": True}


@router.post("/forgot-password", response_model=MessageOut,
             dependencies=[Depends(guard_auth_route)])
def forgot_password(req: ForgotPasswordRequest, request: Request) -> dict:
    """Same reply whether or not the email exists. If it does, the one-time link is
    written to the API console (no email server in this project)."""
    settings = request.app.state.services.settings
    with _sessions(request)() as s:
        user = s.scalars(select(User).where(User.email == req.email)).first()
        if user is not None:
            token = new_token()
            s.add(PasswordReset(token_digest=token_digest(token), user_id=user.user_id,
                                expires_at=utc_now() + timedelta(minutes=settings.reset_link_minutes)))
            s.commit()
            write_audit_log(s, user.user_id, "password_reset_requested", user.user_id)
            link = f"{str(request.base_url).rstrip('/')}/?reset={token}"
            log.warning("\n*** Password reset link for user %s (valid %g minutes; demo: shown "
                        "here instead of emailed):\n    %s\n", user.user_id,
                        settings.reset_link_minutes, link)
    return {"message": RESET_SENT}


@router.post("/reset-password", response_model=PasswordResetOut,
             dependencies=[Depends(guard_auth_route)])
def reset_password(req: ResetPasswordRequest, request: Request) -> dict:
    """Set a new password with a valid, unused link; sign the user out everywhere."""
    problem = password_problem(req.password)
    if problem:
        raise HTTPException(422, problem)
    with _sessions(request)() as s:
        reset = s.get(PasswordReset, token_digest(req.token))
        if reset is None or reset.used_at is not None or reset.expires_at <= utc_now():
            raise HTTPException(400, RESET_INVALID)
        user = s.get(User, reset.user_id)
        user.password_hash = hash_password(req.password)
        reset.used_at = utc_now()
        s.execute(delete(AuthSession).where(AuthSession.user_id == user.user_id))
        s.commit()
        write_audit_log(s, user.user_id, "password_reset_completed", user.user_id)
    return {"reset": True}


# --------------------------------------------------------------------------- start-up

def seed_demo_passwords(sessions, password: str | None) -> tuple[int, int]:
    """Give synthetic demo users that have an email but no password the demo password.

    Users who already have a password (for example after a reset) are left alone.
    Returns (passwords set now, demo users still unable to sign in)."""
    with sessions() as s:
        waiting = list(s.scalars(select(User).where(User.synthetic.is_(True),
                                                    User.email.is_not(None),
                                                    User.password_hash.is_(None))))
        if not password or not waiting:
            return 0, len(waiting)
        for user in waiting:
            user.password_hash = hash_password(password)
        s.commit()
        write_audit_log(s, "system", "demo_passwords_set", f"{len(waiting)} users")
        return len(waiting), 0
