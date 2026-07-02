# backend/agent/agent_decider.py
"""AgentDecider: consumes a queued run's context, drives the PydanticAI
tool loop, persists per-signal verdicts. Called by the worker thread only.
本模块及其 import 链禁止出现任何下单函数（红线）。"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits

from database import get_db
from config import settings
from agent import tools as T
from agent.llm import build_model
from agent.prompts import SYSTEM_PROMPT, PROMPT_VERSION, render_batch
from agent.queue import finish_run, fail_run


class VerdictOut(BaseModel):
    symbol: str
    timeframe: str
    direction: Literal["long", "short", "skip"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: str
    factors: Optional[dict] = None


class DecisionSetOut(BaseModel):
    verdicts: list[VerdictOut]


@dataclass
class Deps:
    budget: T.ToolBudget
    credential_id: Optional[int]
    trace: list = field(default_factory=list)


def _build_agent(cfg, model):
    agent = Agent(model, output_type=DecisionSetOut, system_prompt=SYSTEM_PROMPT, deps_type=Deps)

    @agent.tool
    def get_market_snapshot(ctx: RunContext[Deps], symbols: list[str]) -> dict:
        """获取给定 symbol 列表（clean 格式，如 BTCUSDT）的实时行情快照：价格/24h 涨跌/成交额/资金费率。"""
        out = T.market_snapshot(symbols)
        ctx.deps.trace.append({"tool": "market_snapshot", "args": symbols})
        return out

    @agent.tool
    def get_kline_summary(ctx: RunContext[Deps], symbol: str, interval: str) -> dict:
        """深评一个候选：K线结构摘要（形态/分类/上下方枢轴/ATR/近端统计）。每轮有配额，省着用。"""
        out = T.kline_summary(symbol, interval, ctx.deps.budget)
        ctx.deps.trace.append({"tool": "kline_summary", "args": [symbol, interval],
                               "result": json.dumps(out, ensure_ascii=False)[:2000]})
        return out

    @agent.tool
    def get_signal_history(ctx: RunContext[Deps], symbol: str, indicator: str) -> dict:
        """查询该 symbol×指标 的历史信号 1h/4h/24h 上涨占比与均值（方向盲原始收益）。"""
        out = T.signal_history(symbol, indicator)
        ctx.deps.trace.append({"tool": "signal_history", "args": [symbol, indicator],
                               "result": json.dumps(out, ensure_ascii=False)[:2000]})
        return out

    if cfg.credential_id:
        @agent.tool
        def get_position_plan(ctx: RunContext[Deps], symbol: str, interval: str,
                              direction: Literal["long", "short"]) -> dict:
            """只读仓位规划预览（结构止损/RR/可行性）。不会下单。"""
            out = T.position_plan_preview(symbol, interval, direction, ctx.deps.credential_id)
            ctx.deps.trace.append({"tool": "position_plan", "args": [symbol, interval, direction],
                                   "result": json.dumps(out, ensure_ascii=False)[:2000]})
            return out

    return agent


def _recent_decisions(symbols_tf, cooldown_minutes):
    """{(symbol, timeframe): decision_id} —— 冷却窗口内已有的 agent 裁决。"""
    if not symbols_tf or not cooldown_minutes:
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=cooldown_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    db = get_db(settings.db_path)
    try:
        rows = db.execute(
            """SELECT d.id, d.symbol, d.timeframe FROM agent_decisions d
               JOIN agent_runs r ON r.id = d.run_id
               WHERE r.decider = 'agent' AND d.created_at >= ?""", (cutoff,)).fetchall()
    finally:
        db.close()
    have = {(r["symbol"], r["timeframe"]): r["id"] for r in rows}
    return {k: have[k] for k in symbols_tf if k in have}


def run_agent_on_context(run_id, context, cfg, model_override=None) -> dict:
    try:
        candidates = context.get("candidates", [])
        reused = _recent_decisions({(c["symbol"], c["timeframe"]) for c in candidates},
                                   cfg.cooldown_minutes)
        fresh = [c for c in candidates if (c["symbol"], c["timeframe"]) not in reused]
        trace = {"reused": list(reused.values()), "steps": []}
        n_written = in_tok = out_tok = 0

        if fresh:
            model = model_override or build_model(cfg)
            agent = _build_agent(cfg, model)
            deps = Deps(budget=T.ToolBudget(deep_dive_limit=cfg.deep_dive_limit),
                        credential_id=cfg.credential_id, trace=trace["steps"])
            prompt = render_batch({**context, "candidates": fresh})
            result = agent.run_sync(prompt, deps=deps,
                                    usage_limits=UsageLimits(request_limit=cfg.max_tool_calls))
            usage = result.usage
            in_tok = getattr(usage, "input_tokens", 0) or 0
            out_tok = getattr(usage, "output_tokens", 0) or 0
            known = {(c["symbol"], c["timeframe"]): c for c in fresh}
            db = get_db(settings.db_path)
            try:
                for v in result.output.verdicts:
                    c = known.get((v.symbol, v.timeframe))
                    if not c:
                        trace["steps"].append({"dropped": f"unknown candidate {v.symbol}@{v.timeframe}"})
                        continue
                    ids = c.get("signal_ids", [])
                    db.execute(
                        """INSERT INTO agent_decisions (run_id, signal_id, signal_ids_json, symbol,
                           timeframe, direction, confidence, reasons, factors_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (run_id, ids[0] if ids else None, json.dumps(ids), v.symbol, v.timeframe,
                         v.direction, v.confidence, v.reasons,
                         json.dumps(v.factors, ensure_ascii=False) if v.factors else None))
                    n_written += 1
                db.commit()
            finally:
                db.close()

        finish_run(run_id, model="test" if model_override else cfg.model,
                   prompt_version=PROMPT_VERSION, trace=trace,
                   input_tokens=in_tok, output_tokens=out_tok)
        return {"decisions": n_written, "reused": len(reused)}
    except Exception as e:
        fail_run(run_id, repr(e))
        raise
