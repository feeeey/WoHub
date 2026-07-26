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
from datetime import datetime, timezone
from math import comb
from statistics import mean
from typing import Protocol

# 视界 -> 秒。用作时间簇的桶宽：同一个桶里的信号共享同一段行情。
HORIZON_SECONDS = {"1h": 3600, "4h": 4 * 3600, "24h": 24 * 3600}


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
    - **先按时间簇聚合再做检验**。一次扫描会在几十个高度相关的币种上同时命中，
      同一标的在条件持续时还会连续多根K线重复触发——把每条 signal 行当成独立
      伯努利试验会把 n 虚增一个数量级，p 值随之虚低。模拟显示：对一个毫无方向性
      优势的筛选器，按行计数在 alpha=0.05 下有约 33% 的概率判出「显著」（名义
      应为 2.5%）。因此按视界长度把信号分桶，每个桶的平均收益方向算作一次试验；
    - 簇序列切成 folds 个连续段（walk-forward 风格的分段稳健性检查）：每一段的
      方向都必须与声明一致，防止单一行情段撑起整窗结论；
    - 整窗做精确二项检验（p0=0.5，双侧），alpha 按 spec 内规则数做 Bonferroni
      校正——验证方计入了自己被问了几次；
    - 独立簇数 < min_samples 显式 not_validated：拒绝背书不是失败，是诚实。

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

    def _changes(self, label: str, horizon: str, days: int) -> list[tuple[str, float]]:
        """返回 [(triggered_at, change), …]，按触发时间升序。"""
        from database import get_db
        col = f"change_{horizon}"
        if horizon not in HORIZON_SECONDS:
            raise ValueError(f"未知视界: {horizon}")
        db = get_db(self.db_path)
        try:
            rows = db.execute(
                f"""SELECT s.triggered_at AS ts, o.{col} AS chg
                    FROM signals s JOIN outcomes o ON o.signal_id = s.id
                    WHERE (s.indicator = ? OR s.indicator LIKE ? || '(%')
                      AND o.{col} IS NOT NULL
                      AND s.triggered_at >= datetime('now', ?)
                    ORDER BY s.triggered_at""",
                (label, label, f"-{days} days")).fetchall()
        finally:
            db.close()
        return [(r["ts"], r["chg"]) for r in rows]

    # ---- clustering ----

    @staticmethod
    def _bucket(ts: str, width_s: int) -> tuple[int, object]:
        """把触发时间落到宽度为 width_s 的时间桶。

        返回 (0, 桶序号) 或解析失败时的 (1, 原串)——两段式键让「解析不了的
        时间戳各自成桶」这件事既确定又不会跨类型比较（宁可少合并，也不要把
        不相干的样本硬凑成一次试验）。
        """
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            return (0, int(dt.timestamp()) // width_s)
        return (1, str(ts))

    @classmethod
    def cluster(cls, rows: list[tuple[str, float]], horizon: str) -> list[float]:
        """按视界长度把信号聚成时间簇，每簇取平均收益作为一次独立试验。

        同一批扫描命中的几十个币种、以及条件持续期间同一标的的连续触发，都落在
        同一个桶里——它们反映的是同一段行情，不是几十次独立观测。
        """
        width = HORIZON_SECONDS[horizon]
        buckets: dict[tuple[int, object], list[float]] = {}
        for ts, chg in rows:
            buckets.setdefault(cls._bucket(ts, width), []).append(chg)
        # 按键排序 = 按时间升序，连续折检查依赖这个顺序
        return [mean(buckets[k]) for k in sorted(buckets)]

    # ---- validation ----

    def _validate_rule(self, rule: dict, days: int, alpha_adj: float) -> dict:
        label = rule["label"]
        bias = rule["bias"]
        horizon = rule.get("horizon", self.default_horizon)
        if bias not in ("long", "short"):
            return {"label": label, "verdict": "not_validated",
                    "detail": f"bias 必须是 long/short，收到 {bias!r}"}

        rows = self._changes(label, horizon, days)
        # 检验对象是时间簇，不是原始信号行——见类文档字符串。
        changes = self.cluster(rows, horizon)
        n_rows, n = len(rows), len(changes)
        if n < self.min_samples:
            return {"label": label, "verdict": "not_validated",
                    "n": n, "n_rows": n_rows,
                    "detail": f"独立时段不足（{n} 个 < {self.min_samples}；"
                              f"原始信号 {n_rows} 条，同一时段内的重复触发不算独立样本），"
                              f"拒绝背书"}

        def hit(c):
            return c > 0 if bias == "long" else c < 0

        hits = sum(1 for c in changes if hit(c))
        # 二项检验直接用声明方向的命中数：change==0 保守地不计为命中，
        # 若改用「上涨数」在有零值时与 hit_rate 的分母不一致。
        p = _binom_two_sided(n, hits)

        # 连续折：每段方向都要与声明一致
        size = n // self.folds
        fold_rates, folds_ok = [], True
        for i in range(self.folds):
            seg = changes[i * size:] if i == self.folds - 1 else changes[i * size:(i + 1) * size]
            rate = sum(1 for c in seg if hit(c)) / len(seg)
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
                "n": n, "n_rows": n_rows, "hit_rate": round(hits / n, 4),
                "p_value": round(p, 6),
                "alpha_adjusted": round(alpha_adj, 6), "fold_hit_rates": fold_rates,
                "detail": "；".join(reasons) if reasons else
                          f"分段方向一致且整窗显著（{n} 个独立时段 / {n_rows} 条原始信号；"
                          "方向声明与后验分布一致，≠按此交易可盈利）"}

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
