"""评测执行器。

live 金标实跑复用 runtime 的真实构件（_build_agent / _drive / _DeltaBuffer），
但 deps 换成 EvalDeps（事件进内存列表，不写 chat_* 表）——评测的是同一条
工具循环，不是平行实现。离线模式对存量 chat 轨迹打分，零 LLM 调用。
"""
import asyncio
import json
from dataclasses import dataclass, field

from agent import tools as T
from agent.chat import runtime
from evals import fixtures, scoring
from evals.cases import EvalCase, load_golden
from evals.scoring import CaseResult, DEFAULT_RULES


@dataclass
class EvalDeps:
    """ChatDeps 的鸭子替身：同接口，事件与轨迹只进内存。"""
    budget: T.ToolBudget
    credential_id: int | None = 1
    trace: list = field(default_factory=list)
    events: list = field(default_factory=list)

    def emit(self, type_: str, payload: dict) -> None:
        self.events.append({"type": type_, "payload": payload})

    def check_cancel(self) -> None:
        pass


@dataclass
class EvalConfig:
    """_build_agent/_drive 消费的最小配置面。默认注册全部非视觉工具。"""
    max_tool_calls: int = 15
    deep_dive_limit: int = 5
    credential_id: int | None = 1     # 真值 → 注册 position_plan/account 工具
    vision_model: str = ""            # 评测不跑视觉链
    vision_channel: object = None
    model: str = "eval"


def run_case(case: EvalCase, model, cfg: EvalConfig | None = None,
             use_fixtures: bool = True) -> CaseResult:
    """跑一个金标用例并评分。model 可以是真实 LLM 或 FunctionModel。"""
    cfg = cfg or EvalConfig()
    deps = EvalDeps(budget=T.ToolBudget(deep_dive_limit=cfg.deep_dive_limit),
                    credential_id=cfg.credential_id)
    buf = runtime._DeltaBuffer(deps)

    def _drive():
        agent = runtime._build_agent(cfg, model)
        return asyncio.run(runtime._drive(agent, case.question, deps, cfg, buf))

    if use_fixtures:
        with fixtures.apply():
            _drive()
    else:
        _drive()
    buf.flush()
    return scoring.score_case(case, deps.trace, buf.full_text(),
                              max_tool_calls=cfg.max_tool_calls)


def run_golden(model, cfg: EvalConfig | None = None,
               case_ids: list[str] | None = None) -> list[CaseResult]:
    results = []
    for case in load_golden():
        if case_ids and case.id not in case_ids:
            continue
        try:
            results.append(run_case(case, model, cfg))
        except Exception as e:
            # 单用例崩溃不中断整场评测；崩溃本身就是 0 分信息
            results.append(CaseResult(case_id=case.id, total=0.0,
                                      l1={"score": 0, "violations": [f"运行异常: {e}"]},
                                      l2={"score": 0}, l3={"score": 0},
                                      answer=f"<运行异常: {e}>"))
    return results


# ---- 离线：存量真实轨迹打分 ----

def score_stored(limit: int = 500) -> list[dict]:
    """对 chat_messages 里的 assistant 轨迹打 L2/L3 分（无 L1——存量流量没有期望约束）。

    返回逐条记录，含 prompt_version/model 供分桶。零 LLM 调用。
    """
    from config import settings
    from database import get_db
    db = get_db(settings.db_path)
    try:
        rows = db.execute(
            """SELECT id, content, trace_json, model, created_at FROM chat_messages
               WHERE role = 'assistant' AND trace_json IS NOT NULL
               ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()
    finally:
        db.close()

    out = []
    for r in rows:
        try:
            trace = json.loads(r["trace_json"])
        except json.JSONDecodeError:
            continue
        steps = [s for s in (trace.get("steps") or []) if s.get("tool")]
        answer = r["content"] or ""
        l2 = scoring.score_l2(steps, max_tool_calls=15)
        l3 = scoring.score_l3(answer, steps, DEFAULT_RULES)
        out.append({"message_id": r["id"],
                    "prompt_version": trace.get("prompt_version") or "unknown",
                    "model": r["model"] or "unknown",
                    "created_at": r["created_at"],
                    "n_calls": l2["n_calls"], "repeats": l2["repeats"],
                    "errors": l2["errors"],
                    "l2": l2["score"], "l3": l3["score"], "rules": l3["rules"]})
    return out
