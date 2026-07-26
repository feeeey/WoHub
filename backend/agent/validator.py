"""StrategyValidator 接口与首个真实现 OutcomeValidator。

数据契约：任何要"上升为策略"的逻辑（固化的 prompt 版本、阈值规则、
factor 组合）在启用前必须过一个 StrategyValidator 实现。多重检验校正
（试了多少次必须计入）是验证器实现方的责任，不是调用方的。

strategy_spec 契约（dict）:
    name: str                     策略名
    prompt_version: str | None    若为 prompt 固化
    rules: list                   结构化规则/因子描述（由实现方定义粒度）
    sample_window: str | None     声明的样本窗口（如 '90d'）
verdict ∈ {'pass', 'fail', 'not_validated'}
"""
from dataclasses import dataclass, field
from math import comb
from typing import Protocol


@dataclass
class ValidationReport:
    verdict: str                  # pass | fail | not_validated
    detail: str = ""
    metrics: dict = field(default_factory=dict)


class StrategyValidator(Protocol):
    def validate(self, strategy_spec: dict) -> ValidationReport: ...


class NullValidator:
    """占位实现：显式拒绝背书。存在的意义是让调用方今天就能写依赖注入代码。"""

    def validate(self, strategy_spec: dict) -> ValidationReport:
        return ValidationReport(
            verdict="not_validated",
            detail="尚无验证器实现——策略逻辑未经 walk-forward 验证，不得视为已确认",
        )


def _binom_two_sided(n: int, k: int) -> float:
    """精确二项检验双侧 p 值（p0=0.5）。纯整数运算避免大 n 下的浮点上溢。"""
    if n == 0:
        return 1.0
    total = 2 ** n
    tail_up = sum(comb(n, i) for i in range(k, n + 1))
    tail_down = sum(comb(n, i) for i in range(0, k + 1))
    return min(1.0, 2 * min(tail_up / total, tail_down / total))


class OutcomeValidator:
    """用 signals×outcomes 的真实后验数据验证语义档案的方向（bias）声明。

    方法（刻意保守）：
    - 窗口内按触发时间排序，切成 folds 个连续段（walk-forward 风格的分段
      稳健性检查）：每一段的方向都必须与声明一致，防止单一行情段撑起整窗结论；
    - 整窗做精确二项检验（p0=0.5，双侧），alpha 按 spec 内规则数做 Bonferroni
      校正——验证方计入了自己被问了几次；
    - 样本 < min_samples 显式 not_validated：拒绝背书不是失败，是诚实。

    数据是方向盲原始收益：bias=long 要求上涨占比显著 >50%，bias=short 相反。
    change==0 两个方向都不计为命中（保守）。

    局限（同样要明说）：outcomes 只有 1h/4h/24h 三个固定视界的原始收益，
    没有止损/手续费/滑点，因此 pass 含义是「方向声明与后验分布显著一致」，
    不是「按此交易可盈利」。
    """

    def __init__(self, db_path=None, min_samples: int = 30, folds: int = 3,
                 alpha: float = 0.05, default_horizon: str = "4h"):
        from config import settings
        self.db_path = db_path or settings.db_path
        self.min_samples = min_samples
        self.folds = max(2, folds)
        self.alpha = alpha
        self.default_horizon = default_horizon

    # ---- data ----

    def _changes(self, label: str, horizon: str, days: int) -> list[float]:
        from database import get_db
        col = f"change_{horizon}"
        if horizon not in ("1h", "4h", "24h"):
            raise ValueError(f"未知视界: {horizon}")
        db = get_db(self.db_path)
        try:
            rows = db.execute(
                f"""SELECT o.{col} AS chg
                    FROM signals s JOIN outcomes o ON o.signal_id = s.id
                    WHERE (s.indicator = ? OR s.indicator LIKE ? || '(%')
                      AND o.{col} IS NOT NULL
                      AND s.triggered_at >= datetime('now', ?)
                    ORDER BY s.triggered_at""",
                (label, label, f"-{days} days")).fetchall()
        finally:
            db.close()
        return [r["chg"] for r in rows]

    # ---- validation ----

    def _validate_rule(self, rule: dict, days: int, alpha_adj: float) -> dict:
        label = rule["label"]
        bias = rule["bias"]
        horizon = rule.get("horizon", self.default_horizon)
        if bias not in ("long", "short"):
            return {"label": label, "verdict": "not_validated",
                    "detail": f"bias 必须是 long/short，收到 {bias!r}"}

        changes = self._changes(label, horizon, days)
        n = len(changes)
        if n < self.min_samples:
            return {"label": label, "verdict": "not_validated", "n": n,
                    "detail": f"样本不足（n={n} < {self.min_samples}），拒绝背书"}

        hits = sum(1 for c in changes if (c > 0 if bias == "long" else c < 0))
        p = _binom_two_sided(n, sum(1 for c in changes if c > 0))

        # 连续折：每段方向都要与声明一致
        size = n // self.folds
        fold_rates, folds_ok = [], True
        for i in range(self.folds):
            seg = changes[i * size:] if i == self.folds - 1 else changes[i * size:(i + 1) * size]
            rate = sum(1 for c in seg if (c > 0 if bias == "long" else c < 0)) / len(seg)
            fold_rates.append(round(rate, 4))
            if rate <= 0.5:
                folds_ok = False

        significant = p < alpha_adj and hits / n > 0.5
        verdict = "pass" if (folds_ok and significant) else "fail"
        reasons = []
        if not folds_ok:
            reasons.append(f"分段方向不一致（各折命中率 {fold_rates}）")
        if not significant:
            reasons.append(f"整窗不显著（hit_rate={hits / n:.3f}, p={p:.4f}, "
                           f"校正后 alpha={alpha_adj:.4f}）")
        return {"label": label, "bias": bias, "horizon": horizon, "verdict": verdict,
                "n": n, "hit_rate": round(hits / n, 4), "p_value": round(p, 6),
                "alpha_adjusted": round(alpha_adj, 6), "fold_hit_rates": fold_rates,
                "detail": "；".join(reasons) if reasons else
                          "分段方向一致且整窗显著（方向声明与后验分布一致，"
                          "≠按此交易可盈利）"}

    def validate(self, strategy_spec: dict) -> ValidationReport:
        rules = strategy_spec.get("rules") or []
        if not rules:
            return ValidationReport(verdict="not_validated", detail="spec 无 rules 可验证")
        window = strategy_spec.get("sample_window") or "90d"
        try:
            days = max(7, min(int(str(window).rstrip("dD")), 365))
        except ValueError:
            return ValidationReport(verdict="not_validated",
                                    detail=f"sample_window 无法解析: {window!r}")

        alpha_adj = self.alpha / len(rules)   # Bonferroni：按被验证的规则数校正
        results = [self._validate_rule(r, days, alpha_adj) for r in rules]

        if any(r["verdict"] == "fail" for r in results):
            verdict = "fail"
        elif any(r["verdict"] == "not_validated" for r in results):
            verdict = "not_validated"
        else:
            verdict = "pass"
        summary = "，".join(f"{r['label']}:{r['verdict']}" for r in results)
        return ValidationReport(verdict=verdict, detail=summary,
                                metrics={"window_days": days, "rules": results})
