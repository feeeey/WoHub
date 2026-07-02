"""Decision seam. RuleDecider reproduces the executor's former inline
threshold logic verbatim (golden-tested); AgentDecider (Phase 2) plugs in
behind the same SignalBatch input, asynchronously."""
from dataclasses import dataclass, field

# Screener key -> direction bias. Only unambiguous screeners are mapped;
# labels absent here contribute no direction to rule-baseline decisions.
SCREENER_BIAS = {
    "oscillator/divergence_top": "short",
    "oscillator/divergence_bottom": "long",
    "oscillator/overbought_zone": "short",
    "oscillator/oversold_zone": "long",
}


def bias_map_for(screeners: list[dict]) -> dict[str, str]:
    """label -> 'long'|'short' from task config screeners list."""
    out = {}
    for sc in screeners:
        key = f"{sc.get('folder_type', '')}/{sc.get('screener_name', '')}"
        if key in SCREENER_BIAS:
            out[sc.get("label", sc.get("screener_name", ""))] = SCREENER_BIAS[key]
    return out


@dataclass
class SignalBatch:
    task_id: int
    task_type: str                    # watchlist_signal | market_scan
    config: dict                      # task config (resolutions, overlap_threshold, screeners...)
    results: list                     # [{label, resolution, symbols, count}]
    bias_map: dict = field(default_factory=dict)
    cross: dict = field(default_factory=dict)   # build_cross_analysis output (market_scan)


@dataclass
class Decision:
    symbol: str          # raw form, e.g. 'BINANCE:BTCUSDT.P'
    timeframe: str
    direction: str       # long | short | skip
    confidence: float | None
    reasons: str
    labels: list


@dataclass
class DeciderOutput:
    signals_by_res: dict = field(default_factory=dict)  # watchlist: {res: {sym: [labels]}}
    overlaps: dict = field(default_factory=dict)        # market_scan: {sym: [labels]}
    decisions: list = field(default_factory=list)


def _rule_decision(sym, res, labels, bias_map, reason):
    biases = {bias_map[l] for l in labels if l in bias_map}
    direction = biases.pop() if len(biases) == 1 else "skip"
    return Decision(symbol=sym, timeframe=res, direction=direction,
                    confidence=None, reasons=reason, labels=list(labels))


class RuleDecider:
    def decide(self, batch: SignalBatch) -> DeciderOutput:
        if batch.task_type == "market_scan":
            return self._market_scan(batch)
        return self._watchlist(batch)

    def _watchlist(self, batch):
        resolutions = batch.config.get("resolutions", ["1h"])
        threshold = batch.config.get("overlap_threshold", 2)
        is_single = len(batch.config.get("screeners", [])) <= 1
        signals_by_res = {}
        for res in resolutions:
            res_results = [r for r in batch.results if r["resolution"] == res]
            sym_labels = {}
            for r in res_results:
                for sym in r["symbols"]:
                    sym_labels.setdefault(sym, []).append(r["label"])
            if is_single:
                signals_by_res[res] = sym_labels
            else:
                signals_by_res[res] = {s: l for s, l in sym_labels.items() if len(l) >= threshold}
        decisions = [
            _rule_decision(sym, res, labels, batch.bias_map,
                           f"规则：{len(labels)} 个筛选器命中（阈值 {threshold}）")
            for res, sigs in signals_by_res.items() for sym, labels in sigs.items()
        ]
        return DeciderOutput(signals_by_res=signals_by_res, decisions=decisions)

    def _market_scan(self, batch):
        from sources.pine_screener import build_cross_analysis
        threshold = batch.config.get("overlap_threshold", 2)
        analysis = batch.cross or build_cross_analysis(batch.results)
        batch.cross = analysis   # 回填，供后续 agent 入队复用（Task 14）
        overlaps = {s: l for s, l in analysis.get("screener_overlap", {}).items() if len(l) >= threshold}
        # timeframe per decision: the resolutions this symbol actually hit
        decisions = []
        for sym, labels in overlaps.items():
            hit_res = sorted({r["resolution"] for r in batch.results if sym in r["symbols"]})
            for res in hit_res:
                decisions.append(_rule_decision(sym, res, labels, batch.bias_map,
                                                f"规则：跨筛选器叠加 {len(labels)}（阈值 {threshold}）"))
        return DeciderOutput(overlaps=overlaps, decisions=decisions)
