from agent.validator import NullValidator, OutcomeValidator, _binom_two_sided
from config import settings
from database import get_db


def _seed(label, changes, hours_span=400):
    """按时间升序造 signals+outcomes（间隔均匀，供连续折切分）。"""
    db = get_db(settings.db_path)
    try:
        step = hours_span / max(len(changes), 1)
        for i, chg in enumerate(changes):
            cur = db.execute(
                "INSERT INTO signals (symbol, exchange, indicator, timeframe, triggered_at) "
                "VALUES ('BTCUSDT', 'Binance', ?, '1h', datetime('now', ?))",
                (label, f"-{hours_span - i * step:.0f} hours"))
            db.execute("INSERT INTO outcomes (signal_id, change_4h) VALUES (?, ?)",
                       (cur.lastrowid, chg))
        db.commit()
    finally:
        db.close()


def test_binom_two_sided_sanity():
    assert _binom_two_sided(100, 50) > 0.9           # 五五开：不显著
    assert _binom_two_sided(100, 80) < 0.001         # 80/100：极显著
    assert _binom_two_sided(100, 20) < 0.001         # 对称：反向同样显著
    assert _binom_two_sided(0, 0) == 1.0


def test_null_validator_refuses():
    assert NullValidator().validate({"name": "x"}).verdict == "not_validated"


def test_pass_when_folds_consistent_and_significant():
    # (u,u,d) 循环：每折命中率恒 2/3，n=60 k=40 → p≈0.013 < 0.05
    _seed("强多头", [1.0, 1.0, -1.0] * 20)
    v = OutcomeValidator(min_samples=30, folds=3)
    report = v.validate({"name": "t", "sample_window": "90d",
                         "rules": [{"label": "强多头", "bias": "long"}]})
    assert report.verdict == "pass"
    r = report.metrics["rules"][0]
    assert r["n"] == 60 and r["hit_rate"] == round(40 / 60, 4)
    assert all(fr > 0.5 for fr in r["fold_hit_rates"])


def test_fail_when_folds_disagree():
    # 前半全涨后半全跌：整窗 50%，且分段方向矛盾
    _seed("翻脸信号", [1.0] * 30 + [-1.0] * 30)
    v = OutcomeValidator(min_samples=30, folds=3)
    report = v.validate({"name": "t", "rules": [{"label": "翻脸信号", "bias": "long"}]})
    assert report.verdict == "fail"
    assert "分段方向不一致" in report.metrics["rules"][0]["detail"]


def test_fail_when_not_significant():
    # (u,u,d,d,u) 循环：60% 命中、各折方向一致，但 n=40 时 p≈0.27 不显著
    _seed("弱多头", [1.0, 1.0, -1.0, -1.0, 1.0] * 8)
    v = OutcomeValidator(min_samples=30, folds=3)
    report = v.validate({"name": "t", "rules": [{"label": "弱多头", "bias": "long"}]})
    assert report.verdict == "fail"
    assert "不显著" in report.metrics["rules"][0]["detail"]


def test_short_bias_interpreted_as_down_rate():
    _seed("强空头", [-1.0, -1.0, 1.0] * 20)           # 下跌占比 2/3
    v = OutcomeValidator(min_samples=30, folds=3)
    report = v.validate({"name": "t", "rules": [{"label": "强空头", "bias": "short"}]})
    assert report.verdict == "pass"


def test_insufficient_samples_refused():
    _seed("新信号", [1.0] * 10)
    v = OutcomeValidator(min_samples=30)
    report = v.validate({"name": "t", "rules": [{"label": "新信号", "bias": "long"}]})
    assert report.verdict == "not_validated"
    assert "样本不足" in report.metrics["rules"][0]["detail"]


def test_bonferroni_across_rules():
    """两条规则时 alpha 减半：单独 pass 的边界规则在多规则 spec 里变 fail。

    (u,u,d)×13 → n=39, k=26，精确双侧 p=0.05325。
    alpha=0.06：单规则 0.0533<0.06 → pass；两规则校正后 0.03<0.0533 → fail。
    """
    _seed("边界多头", [1.0, 1.0, -1.0] * 13)
    _seed("边界多头2", [1.0, 1.0, -1.0] * 13)
    v = OutcomeValidator(min_samples=30, folds=3, alpha=0.06)

    solo = v.validate({"name": "t", "rules": [{"label": "边界多头", "bias": "long"}]})
    assert solo.verdict == "pass"

    both = v.validate({"name": "t", "rules": [
        {"label": "边界多头", "bias": "long"},
        {"label": "边界多头2", "bias": "long"}]})
    assert both.verdict == "fail"
    assert all("不显著" in r["detail"] for r in both.metrics["rules"])


def test_bad_spec_inputs():
    v = OutcomeValidator()
    assert v.validate({"name": "t"}).verdict == "not_validated"
    assert v.validate({"name": "t", "rules": [{"label": "x", "bias": "sideways"}]}
                      ).verdict == "not_validated"
    assert v.validate({"name": "t", "sample_window": "abc",
                       "rules": [{"label": "x", "bias": "long"}]}
                      ).verdict == "not_validated"
