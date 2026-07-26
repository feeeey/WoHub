import hmac
import threading
import time
from fastapi import APIRouter, Form, Request, Response, HTTPException, Cookie
from itsdangerous import URLSafeTimedSerializer, BadSignature
from app_logger import log as applog
from config import settings
from typing import Optional

router = APIRouter(prefix="/auth")

SESSION_MAX_AGE = 86400 * 7  # 7 days

# 暴力破解防护。整个应用只有一个共享口令，登录接口又不限速，离线爆破的唯一
# 阻力就是网络往返——默认口令 admin 在这种条件下几秒钟就能撞开。
# 连续失败 FAILURE_THRESHOLD 次后按次数指数退避锁定，成功登录立即清零。
# 进程内计数：本应用是单实例部署，够用；重启会清空，但重启本身不是攻击者可控的。
FAILURE_THRESHOLD = 5
LOCKOUT_BASE_S = 30
LOCKOUT_MAX_S = 900
FAILURE_WINDOW_S = 900        # 这么久没有新的失败就重新计数

_serializer = None
_serializer_lock = threading.Lock()

_failures: dict[str, list] = {}      # client -> [失败次数, 最近一次失败时刻]
_failures_lock = threading.Lock()


def _client_key(request: Request) -> str:
    """限流维度。反向代理后面 request.client.host 会变成代理地址，此时优先用
    X-Forwarded-For 的首段——它可伪造，所以只做限流维度，绝不用于鉴权。"""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def _lockout_remaining(client: str) -> int:
    """还需锁定多少秒，0 表示放行。"""
    now = time.monotonic()
    with _failures_lock:
        entry = _failures.get(client)
        if not entry:
            return 0
        count, last = entry
        if now - last > FAILURE_WINDOW_S:
            _failures.pop(client, None)
            return 0
        if count < FAILURE_THRESHOLD:
            return 0
        penalty = min(LOCKOUT_BASE_S * 2 ** (count - FAILURE_THRESHOLD), LOCKOUT_MAX_S)
        return max(0, int(penalty - (now - last)))


def _record_failure(client: str) -> int:
    now = time.monotonic()
    with _failures_lock:
        count, last = _failures.get(client, [0, now])
        if now - last > FAILURE_WINDOW_S:
            count = 0
        count += 1
        _failures[client] = [count, now]
        return count


def _clear_failures(client: str) -> None:
    with _failures_lock:
        _failures.pop(client, None)


def reset_login_throttle() -> None:
    """测试用：清空全部失败计数。"""
    with _failures_lock:
        _failures.clear()


def _get_serializer() -> URLSafeTimedSerializer:
    """Built lazily: settings.session_secret may generate + persist a key on
    first access, which must not happen at import time (tests and tooling
    import this module without a writable data dir)."""
    global _serializer
    if _serializer is None:
        with _serializer_lock:
            if _serializer is None:
                _serializer = URLSafeTimedSerializer(settings.session_secret)
    return _serializer


def reset_serializer() -> None:
    """Drop the cached serializer so a changed session key takes effect."""
    global _serializer
    with _serializer_lock:
        _serializer = None


def _create_session_token() -> str:
    return _get_serializer().dumps({"authenticated": True})


def _verify_session_token(token: str) -> bool:
    try:
        data = _get_serializer().loads(token, max_age=SESSION_MAX_AGE)
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
def login(request: Request, response: Response, password: str = Form(...)):
    client = _client_key(request)
    retry_after = _lockout_remaining(client)
    if retry_after > 0:
        applog("security", "warn",
               f"登录被限流：{client} 仍在锁定中（剩余 {retry_after}s）")
        raise HTTPException(
            status_code=429, detail=f"尝试过于频繁，请 {retry_after} 秒后重试",
            headers={"Retry-After": str(retry_after)})

    if not hmac.compare_digest(password, settings.app_password):
        n = _record_failure(client)
        applog("security", "warn", f"登录失败：{client}（第 {n} 次）")
        raise HTTPException(status_code=401, detail="Invalid password")

    _clear_failures(client)
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
