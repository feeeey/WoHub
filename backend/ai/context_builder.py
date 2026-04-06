import base64
import os
from database import get_db
from config import settings


def build_context(signal_id: int) -> list:
    db = get_db(settings.db_path)

    sig = db.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    if not sig:
        db.close()
        return []

    snap = db.execute("SELECT * FROM snapshots WHERE signal_id = ?", (signal_id,)).fetchone()
    outcome = db.execute("SELECT * FROM outcomes WHERE signal_id = ?", (signal_id,)).fetchone()

    history = db.execute("""
        SELECT s.triggered_at, s.timeframe, o.change_1h, o.change_4h, o.change_24h
        FROM signals s
        LEFT JOIN outcomes o ON o.signal_id = s.id
        WHERE s.symbol = ? AND s.indicator = ? AND s.id != ?
        ORDER BY s.triggered_at DESC LIMIT 10
    """, (sig["symbol"], sig["indicator"], signal_id)).fetchall()

    screenshot = db.execute(
        "SELECT file_path FROM screenshots WHERE signal_id = ?", (signal_id,)
    ).fetchone()

    db.close()

    lines = [
        f"## 信号信息",
        f"- 币种: {sig['symbol']}",
        f"- 交易所: {sig['exchange']}",
        f"- 指标: {sig['indicator']}",
        f"- 时间周期: {sig['timeframe']}",
        f"- 触发时间: {sig['triggered_at']}",
    ]

    if snap:
        lines.extend([
            f"\n## 触发时市场快照",
            f"- 价格: {snap['price']}",
            f"- 24h成交额: {snap['volume_24h']}",
            f"- 24h涨跌: {snap['change_24h']}%",
            f"- 资金费率: {snap['funding_rate']}",
        ])

    if outcome:
        lines.extend([
            f"\n## 信号后续表现",
            f"- 1h后: {outcome['change_1h'] or '待追踪'}%",
            f"- 4h后: {outcome['change_4h'] or '待追踪'}%",
            f"- 24h后: {outcome['change_24h'] or '待追踪'}%",
        ])

    if history:
        lines.append(f"\n## 历史记录（{sig['indicator']} 在 {sig['symbol']} 的最近 {len(history)} 次触发）")
        wins_1h = sum(1 for h in history if h["change_1h"] and h["change_1h"] > 0)
        wins_24h = sum(1 for h in history if h["change_24h"] and h["change_24h"] > 0)
        total_with_outcome = sum(1 for h in history if h["change_1h"] is not None)
        if total_with_outcome > 0:
            lines.append(f"- 1h正收益率: {wins_1h}/{total_with_outcome} ({wins_1h/total_with_outcome*100:.0f}%)")
            lines.append(f"- 24h正收益率: {wins_24h}/{total_with_outcome} ({wins_24h/total_with_outcome*100:.0f}%)")

    text = "\n".join(lines)
    content = [{"type": "text", "text": text}]

    if screenshot:
        img_path = screenshot["file_path"]
        if os.path.isfile(img_path):
            try:
                with open(img_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                })
            except Exception:
                pass

    return [{"role": "user", "content": content}]
