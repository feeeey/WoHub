"""筛选器语义档案：agent 理解基石筛选器的机制。初稿由 Claude 起草，
用户在 Settings UI 审校修改（Task 18）。"""
from database import get_db
from config import settings
from sources.pine_screener import SCREENER_NAMES

_EDITABLE = ("meaning", "bias", "usage", "caveats", "combos")

DEFAULT_SEMANTICS = {
    "oscillator/divergence_top": {
        "meaning": "价格创新高而振荡指标未同步新高：上涨动能衰竭的见顶预警。",
        "bias": "short（做空倾向）",
        "usage": "趋势末端、超买区出现时参考价值最高；等确认K线（如长上影/吞没）再行动。",
        "caveats": "强趋势中背离会连续钝化多次，单独使用胜率一般；背离不是入场信号而是预警。",
        "combos": "叠加超买、长上影、跨周期共振（如 1h+4h 同时背离）可显著提高可信度。"},
    "oscillator/divergence_bottom": {
        "meaning": "价格创新低而振荡指标未同步新低：下跌动能衰竭的筑底预警。",
        "bias": "long（做多倾向）",
        "usage": "急跌后的低位出现时参考价值最高；等确认K线（如长下影/阳包阴）再行动。",
        "caveats": "阴跌趋势里底背离可连续钝化；地位判断需结合更高周期结构。",
        "combos": "叠加超卖、长下影、跨周期共振可显著提高可信度。"},
    "oscillator/overbought_zone": {
        "meaning": "振荡指标进入高位区间：短期涨幅过大，存在回调压力。",
        "bias": "short（回调倾向，弱信号）",
        "usage": "震荡市里逢高减仓/等回调的参考；本身不构成做空依据。",
        "caveats": "强趋势中超买可长期钝化——顺势行情里做空超买是常见亏损来源。",
        "combos": "与顶背离、长上影叠加才有交易价值；单独出现只做观察。"},
    "oscillator/oversold_zone": {
        "meaning": "振荡指标进入低位区间：短期跌幅过大，存在反弹动能。",
        "bias": "long（反弹倾向，弱信号）",
        "usage": "震荡市里逢低布局的参考；本身不构成做多依据。",
        "caveats": "瀑布行情中超卖会持续钝化；抄底需等待止跌结构。",
        "combos": "与底背离、长下影叠加才有交易价值；单独出现只做观察。"},
    "oscillator/volatility_alert": {
        "meaning": "波动率异常放大（如带宽/ATR 突增）：行情启动或恐慌释放。",
        "bias": "中性（无方向信息）",
        "usage": "提示『这里有事发生』，用于把注意力引向该标的，方向要靠其他信号判定。",
        "caveats": "不含方向；消息面驱动的脉冲也会触发（本系统不做消息面判断）。",
        "combos": "与趋势爆量同时出现提示新趋势启动；与背离叠加提示反转加速。"},
    "oscillator/divergence": {
        "meaning": "顶背离与底背离的合并基础筛选器（方向未拆分，前端默认隐藏）。",
        "bias": "双向（看具体命中方向）",
        "usage": "旧任务兼容用；分析时优先使用方向拆分后的顶背离/底背离。",
        "caveats": "命中结果不区分顶/底，直接使用会丢失方向信息。",
        "combos": "同顶背离/底背离。"},
    "trend/shadows": {
        "meaning": "单根K线出现极端长上影或长下影：该价格区被明确拒绝。",
        "bias": "双向（长上影偏空、长下影偏多）",
        "usage": "出现在关键结构位（前高前低、枢轴）时最有效，是入场确认信号之一。",
        "caveats": "趋势中继也常见影线（洗盘），孤立K线的影线意义有限。",
        "combos": "与超买/超卖、背离、结构枢轴位叠加构成完整的反转确认链。"},
    "trend/trend_volume_spike": {
        "meaning": "放量伴随趋势方向突破：资金参与的趋势加速/启动信号。",
        "bias": "顺势双向（突破方向）",
        "usage": "确认突破有效性、识别主升/主跌段启动；顺势跟随优于逆势。",
        "caveats": "高位放量可能是出货（量价背离）；低流动性币放量易被操纵。",
        "combos": "与波动警报共振提示新趋势；结合资金费率判断拥挤度。"},
}


def seed_defaults() -> int:
    db = get_db(settings.db_path)
    try:
        n = 0
        for key, f in DEFAULT_SEMANTICS.items():
            cur = db.execute(
                """INSERT OR IGNORE INTO screener_semantics
                   (key, meaning, bias, usage, caveats, combos)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (key, f["meaning"], f["bias"], f["usage"], f["caveats"], f["combos"]))
            n += cur.rowcount
        db.commit()
        return n
    finally:
        db.close()


def get_all() -> list[dict]:
    db = get_db(settings.db_path)
    try:
        rows = db.execute("SELECT * FROM screener_semantics ORDER BY key").fetchall()
    finally:
        db.close()
    out = []
    for r in rows:
        d = dict(r)
        d["label"] = SCREENER_NAMES.get(d["key"], d["key"])
        out.append(d)
    return out


def get_all_map() -> dict:
    return {r["key"]: r for r in get_all()}


def upsert(key: str, fields: dict) -> bool:
    if key not in SCREENER_NAMES:
        return False
    sets, params = [], []
    for f in _EDITABLE:
        if f in fields and fields[f] is not None:
            sets.append(f"{f} = ?")
            params.append(str(fields[f]))
    if not sets:
        return True
    db = get_db(settings.db_path)
    try:
        db.execute("INSERT OR IGNORE INTO screener_semantics (key) VALUES (?)", (key,))
        db.execute(f"UPDATE screener_semantics SET {', '.join(sets)}, "
                   "updated_at = datetime('now') WHERE key = ?", (*params, key))
        db.commit()
        return True
    finally:
        db.close()
