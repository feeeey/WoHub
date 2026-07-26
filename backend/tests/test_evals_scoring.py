from evals.cases import EvalCase, load_golden
from evals import scoring


def _case(**kw):
    return EvalCase(id=kw.pop("id", "t"), question=kw.pop("question", "q"), **kw)


def _step(tool, args=None, error=False):
    result = '{"error": "boom"}' if error else '{"ok": true, "data": 1}'
    return {"tool": tool, "args": args or {}, "result": result}


# ---- L1 ----

def test_l1_all_constraint_types():
    case = _case(must_call=["market_overview"],
                 one_of=[["get_klines", "kline_structure"]],
                 must_not_call=["run_screener_scan"])
    steps = [_step("market_overview"), _step("get_klines")]
    r = scoring.score_l1(case, steps)
    assert r["score"] == 1.0 and r["violations"] == []


def test_l1_violations():
    case = _case(must_call=["market_overview"],
                 must_not_call=["run_screener_scan"])
    steps = [_step("run_screener_scan")]
    r = scoring.score_l1(case, steps)
    assert r["score"] == 0.0
    assert len(r["violations"]) == 2      # 缺必调 + 踩禁用


def test_l1_no_constraints_returns_none():
    r = scoring.score_l1(_case(), [_step("anything")])
    assert r["score"] is None             # 无约束 ≠ 满分，是不参与


# ---- L2 ----

def test_l2_clean_trajectory():
    r = scoring.score_l2([_step("a"), _step("b")], max_tool_calls=15)
    assert r["score"] == 1.0 and r["repeats"] == 0


def test_l2_penalizes_wasteful_repeat():
    steps = [_step("get_klines", {"symbol": "BTCUSDT"}),
             _step("get_klines", {"symbol": "BTCUSDT"})]     # 成功后原样重调
    r = scoring.score_l2(steps, max_tool_calls=15)
    assert r["repeats"] == 1 and r["score"] == 0.75


def test_l2_retry_after_error_not_penalized():
    steps = [_step("get_klines", {"symbol": "BTCUSDT"}, error=True),
             _step("get_klines", {"symbol": "BTCUSDT"})]     # 失败后重试是合理行为
    r = scoring.score_l2(steps, max_tool_calls=15)
    assert r["repeats"] == 0 and r["errors"] == 1 and r["score"] == 1.0


def test_l2_over_budget_penalty():
    steps = [_step(f"t{i}") for i in range(6)]
    r = scoring.score_l2(steps, max_tool_calls=5)
    assert r["score"] == 0.5


# ---- L3 ----

def test_l3_good_chinese_analysis():
    answer = "结论：BTCUSDT 1小时结构偏多。ATR 420.5，下方枢轴 63590，RSI 57.3。证据充分。"
    r = scoring.score_l3(answer, [], ["nonempty_conclusion", "cn_language",
                                      "numeric_evidence", "no_execution_claim"])
    assert r["score"] == 1.0


def test_l3_execution_claim_caught():
    for bad in ["已帮你下单做多 BTCUSDT", "订单已提交，等待成交", "我已经为您开仓"]:
        r = scoring.score_l3(bad, [], ["no_execution_claim"])
        assert r["score"] == 0.0, bad


def test_l3_advising_trade_is_not_execution_claim():
    ok = "建议你到交易终端人工确认后执行：/trade?symbol=BTCUSDT&direction=long"
    r = scoring.score_l3(ok, [], ["no_execution_claim", "execution_redirect"])
    assert r["score"] == 1.0


def test_l3_uncertainty_rule_only_binds_when_all_tools_failed():
    failed = [_step("a", error=True), _step("b", error=True)]
    confident = "综合来看强烈看多，直接干。"
    honest = "两个数据源都失败了，证据不足，无法给出结论。"
    assert scoring.score_l3(confident, failed,
                            ["uncertainty_when_tools_failed"])["score"] == 0.0
    assert scoring.score_l3(honest, failed,
                            ["uncertainty_when_tools_failed"])["score"] == 1.0
    # 有成功调用时该规则不约束
    mixed = [_step("a", error=True), _step("b")]
    assert scoring.score_l3(confident, mixed,
                            ["uncertainty_when_tools_failed"])["score"] == 1.0


def test_l3_cn_language():
    assert scoring.score_l3("This is an English answer with numbers 42 and 65.",
                            [], ["cn_language"])["score"] == 0.0


# ---- 汇总与金标集完整性 ----

def test_score_case_weighting():
    case = _case(must_call=["market_overview"],
                 answer_rules=["nonempty_conclusion", "cn_language"])
    steps = [_step("market_overview")]
    answer = "结论：市场整体上涨，涨幅榜前列是 EULUSDT，跌幅集中在 DEXEUSDT，成交量温和放大。"
    r = scoring.score_case(case, steps, answer)
    assert r.total == 1.0
    r2 = scoring.score_case(case, [], answer)
    assert r2.total < 1.0 and r2.l1["score"] == 0.0


def test_golden_set_loads_and_is_valid():
    cases = load_golden()
    assert len(cases) >= 10
    known_rules = set(scoring.RULES)
    for c in cases:
        assert c.id and c.question
        for rule in c.answer_rules:
            assert rule in known_rules, f"{c.id} 引用未知规则 {rule}"
