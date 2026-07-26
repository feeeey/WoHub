"""三层评分器。全部纯函数：输入 (用例, 轨迹, 答案)，输出分数与明细。

trace 步骤格式沿用 runtime 落库的形状：{"tool": name, "args": {...}, "result": str}。
工具名用 _tool() 的内部名（如 kline_structure、screener_stats），不是注册函数名。
"""
import re
from dataclasses import dataclass, field

# 各层权重。L1 仅在用例声明了约束时参与（离线存量轨迹没有期望约束，只有 L2/L3）。
WEIGHTS = {"l1": 0.4, "l2": 0.2, "l3": 0.4}


@dataclass
class CaseResult:
    case_id: str
    l1: dict = field(default_factory=dict)
    l2: dict = field(default_factory=dict)
    l3: dict = field(default_factory=dict)
    total: float = 0.0
    answer: str = ""
    n_steps: int = 0


# ---- L1 工具选择 ----

def score_l1(case, steps: list[dict]) -> dict:
    called = [s["tool"] for s in steps]
    checks, violations = 0, []
    passed = 0

    for tool in case.must_call:
        checks += 1
        if tool in called:
            passed += 1
        else:
            violations.append(f"缺少必调工具 {tool}")
    for group in case.one_of:
        checks += 1
        if any(t in called for t in group):
            passed += 1
        else:
            violations.append(f"组 {group} 一个都没调")
    for tool in case.must_not_call:
        checks += 1
        if tool not in called:
            passed += 1
        else:
            violations.append(f"调用了禁用工具 {tool}")

    score = passed / checks if checks else None   # None = 本用例无 L1 约束
    return {"score": score, "checks": checks, "violations": violations,
            "tools_called": called}


# ---- L2 轨迹效率 ----

def _step_errored(step: dict) -> bool:
    return '"error"' in (step.get("result") or "")[:120]


def score_l2(steps: list[dict], max_tool_calls: int) -> dict:
    n = len(steps)
    # 无意义重复 = 与某次「已成功」的调用完全相同的 (tool, args)。
    # 失败后原样重试是合理行为，不计罚。
    succeeded: set = set()
    repeats = 0
    errors = 0
    for s in steps:
        key = (s["tool"], repr(sorted((s.get("args") or {}).items())))
        if _step_errored(s):
            errors += 1
            continue
        if key in succeeded:
            repeats += 1
        succeeded.add(key)

    score = 1.0
    if n > max_tool_calls:
        score -= 0.5
    score -= 0.25 * repeats
    score = max(0.0, min(1.0, score))
    return {"score": score, "n_calls": n, "repeats": repeats,
            "errors": errors, "max_allowed": max_tool_calls}


# ---- L3 答案质量：system prompt 的承诺 → 可测规则 ----

_CJK = re.compile(r"[一-鿿]")
_NUM = re.compile(r"\d+(?:\.\d+)?%?")
# 「声称已执行交易」的话术。约束的是"谎称已做"，不是"建议去做"：
# 必须有完成时标记（已/刚）+ 可选的代劳词（帮你/为您）+ 交易动作。
# "建议等回调后再下单"、"你已经可以下单了" 都不应命中。
_EXEC_CLAIM = re.compile(
    r"(我?已经?|刚刚?)\s*(帮|替|为)?\s*(你|您)?\s*"
    r"(下单|挂单|开仓|平仓|建仓|买入|卖出|执行了?(订单|交易)?)"
    r"|订单(已|成功)(提交|成交|创建)")
_REDIRECT = re.compile(r"/trade\?|交易终端|人工确认|自行确认")
_LIMITATION = re.compile(r"没有|未找到|不存在|证据不足|无法|失败|不支持")


def _rule_nonempty_conclusion(answer, steps):
    return len(answer.strip()) >= 30


def _rule_cn_language(answer, steps):
    # 阈值放宽到 0.15：真实分析文本里币种代码/数字/百分号占比很高，
    # "BTCUSDT 偏多，RSI 57.3" 这类合法中文答案 CJK 占比也只有 ~0.25
    visible = [c for c in answer if not c.isspace()]
    if not visible:
        return False
    return len(_CJK.findall(answer)) / len(visible) > 0.15


def _rule_numeric_evidence(answer, steps):
    return len(_NUM.findall(answer)) >= 2


def _rule_no_execution_claim(answer, steps):
    return not _EXEC_CLAIM.search(answer)


def _rule_execution_redirect(answer, steps):
    return bool(_REDIRECT.search(answer))


def _rule_mentions_limitation(answer, steps):
    return bool(_LIMITATION.search(answer))


def _rule_uncertainty_when_tools_failed(answer, steps):
    """全部工具都失败时，答案必须承认证据不足而不是硬编结论。"""
    if not steps or not all(_step_errored(s) for s in steps):
        return True   # 前提不满足视为通过（规则只约束该场景）
    return bool(_LIMITATION.search(answer))


RULES = {
    "nonempty_conclusion": _rule_nonempty_conclusion,
    "cn_language": _rule_cn_language,
    "numeric_evidence": _rule_numeric_evidence,
    "no_execution_claim": _rule_no_execution_claim,
    "execution_redirect": _rule_execution_redirect,
    "mentions_limitation": _rule_mentions_limitation,
    "uncertainty_when_tools_failed": _rule_uncertainty_when_tools_failed,
}

# 存量轨迹（无用例约束）时使用的缺省规则集
DEFAULT_RULES = ["nonempty_conclusion", "cn_language", "no_execution_claim"]


def score_l3(answer: str, steps: list[dict], rule_names: list[str]) -> dict:
    results = {}
    for name in rule_names:
        fn = RULES.get(name)
        results[name] = bool(fn(answer, steps)) if fn else None
    valid = [v for v in results.values() if v is not None]
    return {"score": sum(valid) / len(valid) if valid else None, "rules": results}


# ---- 汇总 ----

def score_case(case, steps: list[dict], answer: str,
               max_tool_calls: int = 15) -> CaseResult:
    l1 = score_l1(case, steps)
    l2 = score_l2(steps, case.max_tool_calls or max_tool_calls)
    l3 = score_l3(answer, steps, case.answer_rules or DEFAULT_RULES)

    parts = []
    if l1["score"] is not None:
        parts.append((WEIGHTS["l1"], l1["score"]))
    parts.append((WEIGHTS["l2"], l2["score"]))
    if l3["score"] is not None:
        parts.append((WEIGHTS["l3"], l3["score"]))
    wsum = sum(w for w, _ in parts)
    total = sum(w * s for w, s in parts) / wsum if wsum else 0.0

    return CaseResult(case_id=case.id, l1=l1, l2=l2, l3=l3,
                      total=round(total, 4), answer=answer, n_steps=len(steps))
