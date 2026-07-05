"""Chat agent 的 system prompt 组装与历史渲染。"""
from datetime import datetime, timezone

CHAT_PROMPT_VERSION = "chat-v1"

_BASE = """你是 WoHub 的加密永续合约技术分析助手，在网页对话里帮用户看盘、跑筛选、分析结构。

硬约束（任何情况下不可违反）：
- 纯技术分析：只依据价格、成交量、衍生指标与市场结构；不引入任何消息面/情绪/链上判断。
- 你没有也永远不会有下单能力：不下单、不改单、不撤单。用户想执行时，引导其去交易终端页人工确认（可给出 /trade?symbol=XXX&direction=long|short 形式的预填链接）。
- 以损定仓：仓位大小永远由结构止损反推（get_position_plan 工具），你不做任何自创的风险/仓位计算。
- 不确定就明说。宁可说『证据不足』也不编造斩钉截铁的结论。

工具使用指引：
- run_screener_scan 是长任务（限流 1 次/2 秒），组合数超过上限会被拒绝；先用 list_watchlists 拿 watchlist_id。
- 筛选结果为空有双义性：可能『无信号』也可能『数据源失败』——看返回里的 errors 字段区分，不要过度解读空集。
- K线形态的方向标签是启发式，不是既定事实。
- get_kline_structure / capture_chart 有每轮配额（深评预算），省着用在最值得的标的上。
- 回答用中文，结论先行，给出可复核的数值证据。"""


def _semantics_block() -> str:
    from agent.chat.semantics import get_all
    rows = get_all()
    if not rows:
        return ""
    lines = ["\n【筛选器语义档案】（这些是本系统内置 Pine 筛选器的含义，跑扫描前先对照）"]
    for r in rows:
        lines.append(f"- {r['label']}（key={r['key']}）：{r['meaning']}"
                     f" 方向：{r['bias']}。用法：{r['usage']}"
                     f" 局限：{r['caveats']} 建议叠加：{r['combos']}")
    return "\n".join(lines)


def build_system_prompt() -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"{_BASE}\n{_semantics_block()}\n\n当前时间：{now}"


def render_history(messages: list[dict]) -> str:
    """最近历史的纯文本渲染：只回灌正文，不回灌工具轨迹（token 纪律）。"""
    lines = []
    for m in messages:
        who = "用户" if m["role"] == "user" else "助手"
        content = (m.get("content") or "").strip()
        if content:
            lines.append(f"{who}：{content}")
    return "\n".join(lines)
