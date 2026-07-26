import pytest
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
    detail = report.metrics["rules"][0]["detail"]
    assert "独立时段不足" in detail and "拒绝背书" in detail


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


# ---- 时间簇：自相关信号不得被当成独立试验 --------------------------------

def _seed_batches(label, batches, per_batch, minutes_apart=1):
    """模拟真实触发形态：每批扫描在同一时刻命中多个相关标的，整批同涨同跌。

    批间隔 24h（远大于 4h 桶宽）保证每批各成一簇；批内相隔几分钟，落进同一簇。
    """
    db = get_db(settings.db_path)
    try:
        for b, direction in enumerate(batches):
            for j in range(per_batch):
                cur = db.execute(
                    "INSERT INTO signals (symbol, exchange, indicator, timeframe, triggered_at) "
                    "VALUES (?, 'Binance', ?, '1h', datetime('now', ?, ?))",
                    (f"SYM{j}USDT", label,
                     f"-{(len(batches) - b) * 24} hours",
                     f"+{j * minutes_apart} minutes"))
                db.execute("INSERT INTO outcomes (signal_id, change_4h) VALUES (?, ?)",
                           (cur.lastrowid, direction))
        db.commit()
    finally:
        db.close()


def test_cluster_collapses_one_scan_into_one_trial():
    rows = [("2026-01-01 00:00:00", 1.0), ("2026-01-01 00:01:00", 1.0),
            ("2026-01-01 00:02:00", 3.0),           # 同一个 4h 桶
            ("2026-01-01 09:00:00", -1.0)]          # 另一个桶
    clusters = OutcomeValidator.cluster(rows, "4h")
    assert clusters == [pytest.approx(5 / 3), -1.0]


def test_cluster_width_follows_horizon():
    rows = [("2026-01-01 00:00:00", 1.0), ("2026-01-01 02:00:00", 1.0)]
    assert len(OutcomeValidator.cluster(rows, "1h")) == 2    # 相隔 2h：两个桶
    assert len(OutcomeValidator.cluster(rows, "4h")) == 1    # 同一个 4h 桶


def test_unparseable_timestamp_is_not_merged():
    rows = [("garbage-a", 1.0), ("garbage-b", -1.0)]
    assert len(OutcomeValidator.cluster(rows, "4h")) == 2


def test_correlated_batch_hits_cannot_manufacture_significance():
    """核心回归：40 批扫描 × 每批 30 个相关标的，24 批涨 16 批跌（60%）。

    同一份数据，两种算法天差地别：
    - 按原始信号行算 n=1200、k=720 → p=4.4e-12，会被判「压倒性显著」；
    - 按独立时段算 n=40、k=24     → p=0.268，正确地判为不显著。
    每折都放 8 个上涨批，使分段检查通过，让判决单独取决于显著性。
    """
    fold = [1.0] * 8 + [-1.0] * 5
    batches = fold + fold + ([1.0] * 8 + [-1.0] * 6)      # 13+13+14 = 40 批
    assert len(batches) == 40 and batches.count(1.0) == 24
    _seed_batches("批量信号", batches, per_batch=30)

    v = OutcomeValidator(min_samples=30, folds=3)
    report = v.validate({"name": "t", "rules": [{"label": "批量信号", "bias": "long"}]})
    r = report.metrics["rules"][0]

    assert r["n_rows"] == 1200, "原始信号数照实记录"
    assert r["n"] == 40, f"应聚合成 40 个独立时段，实得 {r['n']}"
    assert all(fr > 0.5 for fr in r["fold_hit_rates"]), "分段应通过，判决只看显著性"
    assert r["p_value"] > 0.05, f"聚合后不应显著，实得 p={r['p_value']}"
    assert report.verdict == "fail"
    assert "不显著" in r["detail"]

    # 反证：同一批数据按原始行计数会得出完全相反的结论
    assert _binom_two_sided(r["n_rows"], 720) < 1e-9


def test_zero_change_is_not_counted_as_a_hit():
    """change==0 保守地两个方向都不算命中，且检验分母与 hit_rate 一致。"""
    rows = [("2026-01-0%d 00:00:00" % d, 0.0) for d in range(1, 10)]
    clusters = OutcomeValidator.cluster(rows, "24h")
    assert clusters == [0.0] * 9


def test_bad_spec_inputs():
    v = OutcomeValidator()
    assert v.validate({"name": "t"}).verdict == "not_validated"
    assert v.validate({"name": "t", "rules": [{"label": "x", "bias": "sideways"}]}
                      ).verdict == "not_validated"
    assert v.validate({"name": "t", "sample_window": "abc",
                       "rules": [{"label": "x", "bias": "long"}]}
                      ).verdict == "not_validated"
