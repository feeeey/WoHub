# API 服务端鉴权强制 设计

日期：2026-05-31
状态：已与用户确认设计，待写实现计划

## 背景与目标

后端目前**没有任何服务端鉴权**：`backend/auth.py` 定义了 `is_authenticated()` 但只用于 `/auth/status`；`main.py` 没有中间件、`api/__init__.py` 的路由也没有挂任何鉴权依赖。唯一防线是前端 Vue 路由守卫（客户端，可被 `curl /api/trading/order` 直接绕过下真单）。这与 CLAUDE.md、`api/trading.py` docstring 及用户"所有交易端点在 cookie 鉴权之后"的约束相悖，且用户即将做实盘自动交易——属高优先级安全修复。

**目标**：在服务端强制 cookie-session 鉴权，除登录与健康检查外的所有 API 端点未鉴权时返回 401；现有 195 项测试零回归。

## 决策（已确认）

1. **保护范围**：除 `/auth/*` 与 `/health` 外全部需鉴权（market、channels、tasks、settings、ai、scanner、screenshots、klines、trading）。SPA 静态与 `/{path:path}` 兜底在 app 层、天然公开。
2. **机制**：FastAPI 依赖 `require_auth`，挂在一个分组的"保护"子路由上（方案 A）。
3. **测试策略**：`conftest.py` autouse fixture 默认 override `require_auth` 放行，现有测试零改动；专门的 `test_auth_enforcement.py`（带 `no_auth_override` 标记，跳过 override）验证真实鉴权。

## 架构与改动

### 1. `backend/auth.py` — 新增 `require_auth` 依赖
```python
def require_auth(session: Optional[str] = Cookie(None)) -> None:
    if not is_authenticated(session):
        raise HTTPException(status_code=401, detail="Not authenticated")
```
成功返回 None，失败抛 401。复用既有 `is_authenticated`。

### 2. `backend/api/__init__.py` — 公开组 / 保护组
```python
from fastapi import APIRouter, Depends
from auth import router as auth_router, require_auth
...
api_router = APIRouter(prefix="/api")

# 公开（无需鉴权）
api_router.include_router(health_router)
api_router.include_router(auth_router)

# 保护（全部需鉴权）
protected = APIRouter(dependencies=[Depends(require_auth)])
protected.include_router(market_router)
protected.include_router(channels_router)
protected.include_router(tasks_router)
protected.include_router(settings_router)
protected.include_router(ai_router)
protected.include_router(scanner_router)
protected.include_router(screenshots_router)
protected.include_router(klines_router)
protected.include_router(trading_router)
api_router.include_router(protected)
```
各子路由前缀不变 → 最终路径（如 `/api/market/...`、`/api/trading/plan`）保持不变。

### 3. `backend/api/trading.py` — 修正 docstring
将"All endpoints require the standard cookie-session auth (handled at the router level via the existing dependency wiring)"改为如实描述：鉴权由 `api_router` 的保护组 `require_auth` 依赖在路由层强制。

### 4. 数据流与错误
- 未鉴权请求受保护端点 → `401 {"detail": "Not authenticated"}`。
- 前端 `client.js` 的 `request()` 已在收到 401（非 `/auth/`）时 `window.location.href = '/login'`，行为天然对齐，无需改前端。
- SPA 静态与 `/{path:path}` 在 app 层、不受保护组影响，登录页可在未鉴权时加载。

## 测试

### `backend/tests/conftest.py` — autouse override
```python
@pytest.fixture(autouse=True)
def auth_override(request):
    from auth import require_auth
    if "no_auth_override" in request.keywords:
        yield
        return
    app.dependency_overrides[require_auth] = lambda: None
    yield
    app.dependency_overrides.pop(require_auth, None)
```
并注册 `no_auth_override` marker（`pytest.ini`/`pyproject.toml` 或 conftest 的 `pytest_configure`）。现有共用 `client` fixture 的端点测试因此零改动通过。直接调 service 层的测试（test_trading.py 等）走的不是 HTTP 层，本就不受影响。

### `backend/tests/test_auth_enforcement.py`（新增，带 `@pytest.mark.no_auth_override`）
- 保护端点无 cookie → 401：`GET /api/settings/info`（或另一稳定的保护端点）。
- 保护端点带有效 session cookie → 非 401（先 `POST /api/auth/login` 取 cookie）。
- 公开端点无 cookie → 200：`GET /api/health`、`GET /api/auth/status`。
- 可选：`/auth/status` 在未登录时仍返回 `{"authenticated": false}`（确认公开语义不变）。

全部走现有 pytest，`-m "not network"` 可离线运行。

## 范围与非目标
- 不改鉴权模型（仍是单 `APP_PASSWORD` + itsdangerous 签名 cookie）。
- 不改前端（401 跳转已存在）。
- 不动 SPA 静态服务。
- CLAUDE.md 当前未纳入 git，不在本次提交范围。

## 安全说明
此修复使 CLAUDE.md / docstring 中"所有交易端点在 cookie 鉴权之后"的承诺真正成立，关闭了未鉴权下真实下单的暴露面。相关记忆：[[trading-endpoints-no-server-auth]]。
