"""单轮执行器：组装 prompt → PydanticAI agent.iter 工具循环 → 事件落库 →
assistant 消息落库。本模块及 import 链禁止出现任何下单函数（红线）。"""
import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Optional

from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits
from pydantic_ai.messages import PartDeltaEvent, TextPartDelta

from app_logger import log as applog
from agent import tools as T
from agent.llm import build_model
from agent.chat import store, events
from agent.chat.prompts import build_system_prompt, render_history, CHAT_PROMPT_VERSION

HISTORY_LIMIT = 20          # 发给模型的历史消息条数上限（规格 §4）
FLUSH_INTERVAL = 0.1        # text_delta 聚合窗口（秒）
FLUSH_CHARS = 400
RESULT_TRUNC = 2000         # 工具结果截断（trace 与事件摘要）
MODEL_RESULT_CAP = 24000    # 模型侧兜底：防病态大结果撑爆上下文；正常工具输出（如 300 根K线）不受影响


class TurnCancelled(Exception):
    pass


@dataclass
class ChatDeps:
    turn_id: int
    budget: T.ToolBudget
    credential_id: Optional[int]
    trace: list = field(default_factory=list)

    def emit(self, type_: str, payload: dict) -> None:
        events.append_event(self.turn_id, type_, payload)

    def check_cancel(self) -> None:
        if store.cancel_requested(self.turn_id):
            raise TurnCancelled()


class _DeltaBuffer:
    """text_delta 事件聚合：~100ms 或 400 字符一批，不逐 token 写库。"""

    def __init__(self, deps: ChatDeps):
        self.deps = deps
        self.buf = ""
        self.parts: list[str] = []
        self.last = time.monotonic()

    def add(self, text: str) -> None:
        self.buf += text
        if len(self.buf) >= FLUSH_CHARS or time.monotonic() - self.last >= FLUSH_INTERVAL:
            self.flush()
            self.deps.check_cancel()          # 流式块之间的取消检查点

    def flush(self) -> None:
        if self.buf:
            self.deps.emit("text_delta", {"text": self.buf})
            self.parts.append(self.buf)
            self.buf = ""
        self.last = time.monotonic()

    def full_text(self) -> str:
        return "".join(self.parts) + self.buf


def _tool(ctx: RunContext[ChatDeps], name: str, args: dict, fn) -> dict:
    """统一工具包装：取消检查 → tool_start → 执行 → trace/tool_end。
    事件由我们自己发（不依赖框架事件类名，抗版本漂移）。"""
    d = ctx.deps
    d.check_cancel()
    d.emit("tool_start", {"tool": name, "args": args})
    t0 = time.monotonic()
    try:
        out = fn()
    except TurnCancelled:
        raise
    except Exception as e:                      # 工具失败不终止轮次：错误回传模型
        out = {"error": f"工具执行异常: {e}"}
    full = json.dumps(out, ensure_ascii=False)
    ok = not (isinstance(out, dict) and out.get("error"))
    raw = full[:RESULT_TRUNC]
    d.trace.append({"tool": name, "args": args, "result": raw})
    d.emit("tool_end", {"tool": name, "ok": ok, "summary": raw[:400],
                        "elapsed_ms": int((time.monotonic() - t0) * 1000)})
    if len(full) > MODEL_RESULT_CAP:
        return {"_truncated": True,
                "hint": "结果过大已截断，请用更小的参数重试（如降低 limit / 缩小扫描范围）",
                "preview": full[:MODEL_RESULT_CAP]}
    return out


def _build_agent(cfg, model) -> Agent:
    agent = Agent(model, output_type=str, system_prompt=build_system_prompt(),
                  deps_type=ChatDeps)

    @agent.tool
    def get_market_snapshot(ctx: RunContext[ChatDeps], symbols: list[str]) -> dict:
        """实时行情快照：价格/24h涨跌/成交额/资金费率。symbols 用 clean 格式（如 BTCUSDT）。"""
        return _tool(ctx, "market_snapshot", {"symbols": symbols},
                     lambda: T.market_snapshot(symbols))

    @agent.tool
    def get_market_overview(ctx: RunContext[ChatDeps], top_n: int = 10) -> dict:
        """全市场总览：涨跌榜 + 资金费率极值（Binance USDT-M）。"""
        return _tool(ctx, "market_overview", {"top_n": top_n},
                     lambda: T.market_overview(top_n))

    @agent.tool
    def get_klines(ctx: RunContext[ChatDeps], symbol: str, interval: str,
                   limit: int = 100) -> dict:
        """原始K线数组 [[open_time,open,high,low,close,volume],…]，limit≤300。"""
        return _tool(ctx, "get_klines", {"symbol": symbol, "interval": interval,
                                         "limit": limit},
                     lambda: T.get_klines(symbol, interval, limit))

    @agent.tool
    def get_indicators(ctx: RunContext[ChatDeps], symbol: str, interval: str) -> dict:
        """核心指标当前值：MA/EMA/MACD/RSI/BOLL/ATR/量比（基于已收盘K线）。"""
        return _tool(ctx, "get_indicators", {"symbol": symbol, "interval": interval},
                     lambda: T.get_indicators(symbol, interval))

    @agent.tool
    def get_kline_structure(ctx: RunContext[ChatDeps], symbol: str, interval: str) -> dict:
        """K线结构深评：形态/分类/上下方枢轴/ATR/近端统计。有每轮配额，省着用。"""
        return _tool(ctx, "kline_structure", {"symbol": symbol, "interval": interval},
                     lambda: T.kline_summary(symbol, interval, ctx.deps.budget))

    @agent.tool
    def get_signal_history(ctx: RunContext[ChatDeps], symbol: str, indicator: str) -> dict:
        """该 symbol×筛选器label 的历史信号 1h/4h/24h 表现（方向盲原始收益）。"""
        return _tool(ctx, "signal_history", {"symbol": symbol, "indicator": indicator},
                     lambda: T.signal_history(symbol, indicator))

    @agent.tool
    def list_watchlists(ctx: RunContext[ChatDeps]) -> dict:
        """TradingView 关注列表（跑筛选扫描前先用这个拿 watchlist_id）。"""
        return _tool(ctx, "list_watchlists", {}, T.list_watchlists)

    @agent.tool
    def run_screener_scan(ctx: RunContext[ChatDeps], screener_keys: list[str],
                          timeframes: list[str], watchlist_id: int) -> dict:
        """跑 Pine 筛选器扫描（长任务：限流 1 次/2 秒，组合上限 12）。
        screener_keys 用语义档案里的 key（如 oscillator/divergence_bottom）。"""
        d = ctx.deps

        def cb(done, total, note):
            d.check_cancel()
            d.emit("tool_progress", {"tool": "run_screener_scan",
                                     "done": done, "total": total, "note": note})

        return _tool(ctx, "run_screener_scan",
                     {"screener_keys": screener_keys, "timeframes": timeframes,
                      "watchlist_id": watchlist_id},
                     lambda: T.run_screener_scan(screener_keys, timeframes,
                                                 watchlist_id, progress_cb=cb))

    if cfg.credential_id:
        @agent.tool
        def get_position_plan(ctx: RunContext[ChatDeps], symbol: str, interval: str,
                              direction: str) -> dict:
            """只读仓位规划预览（结构止损/RR/可行性）。不会下单。direction: long|short"""
            return _tool(ctx, "position_plan", {"symbol": symbol, "interval": interval,
                                                "direction": direction},
                         lambda: T.position_plan_preview(symbol, interval, direction,
                                                         ctx.deps.credential_id))

        @agent.tool
        def get_account_overview(ctx: RunContext[ChatDeps]) -> dict:
            """当前账户只读总览：余额/持仓/挂单。"""
            return _tool(ctx, "account_overview", {},
                         lambda: T.account_overview(ctx.deps.credential_id))

    return agent


def _build_prompt(session_id: int, user_message_id: int):
    msgs = store.list_messages(session_id)
    current = next(m for m in msgs if m["id"] == user_message_id)
    history = [m for m in msgs if m["id"] < user_message_id][-HISTORY_LIMIT:]
    text = ""
    if history:
        text += "【对话历史（最近 %d 条）】\n%s\n\n【当前消息】\n" % (
            len(history), render_history(history))
    text += current.get("content") or ""
    if current.get("images"):
        # 视觉管道在 Task 14 接入；此前如实告知模型
        text += "\n（用户附带了 %d 张图片，当前部署未启用视觉功能，无法查看图片内容）" \
                % len(current["images"])
    return text, current


async def _drive(agent: Agent, prompt: str, deps: ChatDeps, cfg, buf: _DeltaBuffer):
    async with agent.iter(prompt, deps=deps,
                          usage_limits=UsageLimits(
                              request_limit=cfg.max_tool_calls + 2,
                              tool_calls_limit=cfg.max_tool_calls)) as run:
        async for node in run:
            if Agent.is_model_request_node(node):
                async with node.stream(run.ctx) as stream:
                    async for ev in stream:
                        if isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
                            buf.add(ev.delta.content_delta)
        return run


def _usage_tokens(run) -> tuple[int, int]:
    try:
        u = run.usage() if callable(getattr(run, "usage", None)) else getattr(run, "usage", None)
    except Exception:
        return 0, 0
    if u is None:
        return 0, 0
    it = getattr(u, "input_tokens", None) or getattr(u, "request_tokens", 0) or 0
    ot = getattr(u, "output_tokens", None) or getattr(u, "response_tokens", 0) or 0
    return it, ot


def _finalize_abnormal(deps: ChatDeps, buf: _DeltaBuffer, session_id: int,
                       turn_id: int, status: str, error: str | None) -> None:
    """异常出口的尽力收尾：每步独立兜底，finish_turn 永远最后尝试——
    保证 turn 不会卡死在 running。"""
    try:
        buf.flush()
    except Exception:
        pass
    try:
        content = buf.full_text() or ("（已停止）" if status == "cancelled" else "")
        store.add_message(session_id, "assistant", content,
                          trace={"prompt_version": CHAT_PROMPT_VERSION,
                                 "steps": deps.trace},
                          error="cancelled" if status == "cancelled" else error)
    except Exception as e:
        applog("chat", "error", f"finalize add_message failed: {e!r}")
    try:
        if status == "cancelled":
            deps.emit("cancelled", {})
        else:
            deps.emit("turn_error", {"error": (error or "")[:500]})
    except Exception as e:
        applog("chat", "error", f"finalize emit failed: {e!r}")
    try:
        store.finish_turn(turn_id, status)
    except Exception as e:
        applog("chat", "error", f"finalize finish_turn failed: {e!r}")


def run_turn(turn_row, model_override=None) -> None:
    """worker 线程入口。所有出口都保证：turn 有终态 + 有对应事件。"""
    turn_id, session_id = turn_row["id"], turn_row["session_id"]
    from agent.config import load_config
    cfg = load_config()
    deps = ChatDeps(turn_id=turn_id,
                    budget=T.ToolBudget(deep_dive_limit=cfg.deep_dive_limit),
                    credential_id=cfg.credential_id)
    buf = _DeltaBuffer(deps)
    try:
        if store.cancel_requested(turn_id):
            raise TurnCancelled()
        if not cfg.enabled or (not cfg.api_key and model_override is None):
            raise RuntimeError("Agent 未启用或未配置 API Key（请到系统设置页配置）")
        prompt, current = _build_prompt(session_id, turn_row["user_message_id"])
        model = model_override or build_model(cfg)
        agent = _build_agent(cfg, model)
        run = asyncio.run(_drive(agent, prompt, deps, cfg, buf))
        buf.flush()
        in_tok, out_tok = _usage_tokens(run)
        mid = store.add_message(session_id, "assistant", buf.full_text(),
                                trace={"prompt_version": CHAT_PROMPT_VERSION,
                                       "steps": deps.trace},
                                model="test" if model_override else cfg.model,
                                input_tokens=in_tok, output_tokens=out_tok)
        deps.emit("turn_done", {"message_id": mid,
                                "input_tokens": in_tok, "output_tokens": out_tok})
        store.finish_turn(turn_id, "done")
        try:
            _maybe_autotitle(session_id, current)
        except Exception as e:
            applog("chat", "warn", f"autotitle failed: {e!r}")
    except TurnCancelled:
        _finalize_abnormal(deps, buf, session_id, turn_id, "cancelled", None)
    except Exception as e:
        applog("chat", "error", f"turn #{turn_id} failed: {e!r}")
        _finalize_abnormal(deps, buf, session_id, turn_id, "failed", str(e)[:2000])


def _maybe_autotitle(session_id: int, current: dict) -> None:
    sess = [s for s in store.list_sessions() if s["id"] == session_id]
    if sess and sess[0]["title"] == "新会话":
        text = (current.get("content") or "图片分析").strip()
        store.rename_session(session_id, text[:30])
