import hmac
from fastapi import APIRouter, Form, Request, Response, HTTPException, Cookie
from itsdangerous import URLSafeTimedSerializer, BadSignature
from config import settings
from typing import Optional

router = APIRouter(prefix="/auth")

_serializer = URLSafeTimedSerializer(settings.secret_key)
SESSION_MAX_AGE = 86400 * 7  # 7 days


def _create_session_token() -> str:
    return _serializer.dumps({"authenticated": True})


def _verify_session_token(token: str) -> bool:
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        return data.get("authenticated", False)
    except BadSignature:
        return False


def is_authenticated(session: Optional[str] = Cookie(None)) -> bool:
    if not session:
        return False
    return _verify_session_token(session)


def require_auth(session: Optional[str] = Cookie(None)) -> None:
    """FastAPI dependency that rejects unauthenticated requests with 401.

    Attach to protected routers via `dependencies=[Depends(require_auth)]`.
    Reuses the same session-cookie verification as is_authenticated().
    """
    if not is_authenticated(session):
        raise HTTPException(status_code=401, detail="Not authenticated")


@router.post("/login")
def login(response: Response, password: str = Form(...)):
    if not hmac.compare_digest(password, settings.app_password):
        raise HTTPException(status_code=401, detail="Invalid password")

    token = _create_session_token()
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
    )
    return {"authenticated": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(key="session")
    return {"authenticated": False}


@router.get("/status")
def auth_status(session: Optional[str] = Cookie(None)):
    return {"authenticated": is_authenticated(session)}
