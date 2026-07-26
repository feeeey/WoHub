"""登录接口必须有暴力破解阻力。

整个应用只有一个共享口令，登录接口早期不限速也不记失败日志——离线爆破的唯一
阻力就是网络往返，而默认口令是 `admin`。现在连续失败会指数退避锁定，且每次失败
都写审计日志。
"""
import pytest

import auth
from auth import (FAILURE_THRESHOLD, LOCKOUT_MAX_S, _client_key,
                  _lockout_remaining, _record_failure, reset_login_throttle)


@pytest.fixture(autouse=True)
def _clean():
    reset_login_throttle()
    yield
    reset_login_throttle()


def test_below_threshold_is_not_locked():
    for _ in range(FAILURE_THRESHOLD - 1):
        _record_failure("1.2.3.4")
    assert _lockout_remaining("1.2.3.4") == 0, "少量误输不该把人锁在门外"


def test_locks_out_after_threshold():
    for _ in range(FAILURE_THRESHOLD):
        _record_failure("1.2.3.4")
    assert _lockout_remaining("1.2.3.4") > 0


def test_backoff_grows_and_is_capped():
    prev = 0
    for i in range(FAILURE_THRESHOLD + 8):
        _record_failure("1.2.3.4")
        cur = _lockout_remaining("1.2.3.4")
        assert cur >= prev or cur == LOCKOUT_MAX_S
        prev = cur
    assert prev <= LOCKOUT_MAX_S


def test_lockout_is_per_client():
    for _ in range(FAILURE_THRESHOLD + 2):
        _record_failure("1.1.1.1")
    assert _lockout_remaining("1.1.1.1") > 0
    assert _lockout_remaining("2.2.2.2") == 0, "一个来源被锁不该殃及其他人"


def test_client_key_prefers_forwarded_header():
    class Req:
        headers = {"x-forwarded-for": "9.9.9.9, 10.0.0.1"}
        client = type("C", (), {"host": "172.17.0.1"})()
    assert _client_key(Req()) == "9.9.9.9"


def test_client_key_falls_back_to_peer():
    class Req:
        headers = {}
        client = type("C", (), {"host": "172.17.0.1"})()
    assert _client_key(Req()) == "172.17.0.1"


def test_client_key_survives_missing_client():
    class Req:
        headers = {}
        client = None
    assert _client_key(Req()) == "unknown"


# ---- 端到端 ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_repeated_bad_passwords_start_returning_429(client):
    async with client as c:
        codes = [(await c.post("/api/auth/login", data={"password": "wrong"})).status_code
                 for _ in range(FAILURE_THRESHOLD + 1)]
    assert codes[0] == 401
    assert codes[-1] == 429, f"爆破未被限流：{codes}"


@pytest.mark.asyncio
async def test_successful_login_clears_the_counter(client):
    async with client as c:
        for _ in range(FAILURE_THRESHOLD - 1):
            await c.post("/api/auth/login", data={"password": "wrong"})
        ok = await c.post("/api/auth/login", data={"password": "testpass"})
        assert ok.status_code == 200
        # 计数已清零，后续误输仍从头计算
        again = await c.post("/api/auth/login", data={"password": "wrong"})
        assert again.status_code == 401


@pytest.mark.asyncio
async def test_failed_logins_are_audited(client):
    from app_logger import get_logs
    async with client as c:
        await c.post("/api/auth/login", data={"password": "wrong"})
    assert any("登录失败" in e["message"] for e in get_logs(source="security", limit=50)), \
        "登录失败必须留痕，否则爆破在事后完全不可见"
