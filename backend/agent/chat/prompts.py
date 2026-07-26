"""Chat agent 的 system prompt 组装与历史渲染。"""
from datetime import datetime, timezone

# v2：新增工具轨迹效率纪律（评测发现对比类问题过度采集：8 次调用含重复，
# 其中 5 次 get_indicators 只为对比两个标的）。改动经金标评测前后对比验证。
CHAT_PROMPT_VERSION = "chat-v2"

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
- 工具调用讲效率：动手前先想清楚回答这个问题需要哪几样证据，按最小集合取数。
  多标的行情对比优先用一次 get_market_snapshot（symbols 传数组）拿齐；
  同一轮内不要以相同参数重复调用任何工具（结果不会变）；
  证据够了就作答——"再确认一遍"式的追加调用只烧配额不改结论。
  简单对比/读数类问题通常 2~4 次调用足够。
- 长期记忆：用户明确表达稳定偏好或纠正你的长期性认知时（如「我只做4h以上」
  「别再推荐meme币」），用 remember 存下来；过时了用 forget 删。
  只存长期有效的结论，临时行情观点/单次问题不要存。
- 回答用中文，结论先行，给出可复核的数值证据。"""


def _semantics_block() -> str:
    from agent.chat.semantics import get_all
    rows = get_all()
    if not rows:
        return ""
    # 后验统计注入：语义档案从「静态人写的经验」升级为「带数据的可核陈述」。
    # 统计失败不影响档案本身（闭环是增强，不是依赖）。
    stats = {}
    try:
        from agent.outcome_stats import get_stats, format_stats_line
        stats = get_stats()
    except Exception:
        format_stats_line = None
    lines = ["\n【筛选器语义档案】（这些是本系统内置 Pine 筛选器的含义，跑扫描前先对照。"
             "附带的后验统计是方向盲原始收益——上涨占比不是按信号方向交易的胜率，"
             "做空类信号应关注下跌占比；引用时须连同样本量一起给出）"]
    for r in rows:
        lines.append(f"- {r['label']}（key={r['key']}）：{r['meaning']}"
                     f" 方向：{r['bias']}。用法：{r['usage']}"
                     f" 局限：{r['caveats']} 建议叠加：{r['combos']}")
        if stats and format_stats_line:
            sl = format_stats_line(r["label"], stats)
            if sl:
                lines.append(f"  ↳ {sl}")
    return "\n".join(lines)


def _memory_block() -> str:
    from agent.memory import render_block
    try:
        return render_block()
    except Exception:
        return ""   # 记忆读取失败不阻塞对话


def build_system_prompt() -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"{_BASE}\n{_memory_block()}\n{_semantics_block()}\n\n当前时间：{now}"


# 证据回灌：只给最近 K 条助手消息附工具证据摘要，且逐条截断——
# 解决「刚才那个结构你再看看」要重新调工具的问题，同时不放开 token 闸门
EVIDENCE_LAST_K = 2
EVIDENCE_MAX_STEPS = 3
EVIDENCE_RESULT_CHARS = 110


def _evidence_line(m: dict) -> str | None:
    steps = [s for s in ((m.get("trace") or {}).get("steps") or []) if s.get("tool")]
    if not steps:
        return None
    parts = []
    for s in steps[:EVIDENCE_MAX_STEPS]:
        args = str(s.get("args") or {})[:60]
        result = (s.get("result") or "")[:EVIDENCE_RESULT_CHARS]
        parts.append(f"{s['tool']}{args}→{result}")
    more = f"（另 {len(steps) - EVIDENCE_MAX_STEPS} 步略）" if len(steps) > EVIDENCE_MAX_STEPS else ""
    return "  （本轮已取证据：" + "；".join(parts) + more + "）"


def render_history(messages: list[dict]) -> str:
    """最近历史的纯文本渲染。正文全量回灌；工具证据只给最近
    EVIDENCE_LAST_K 条助手消息附摘要（token 纪律 vs 免重复取数的折中）。"""
    assistant_ids = [m["id"] for m in messages if m["role"] == "assistant"]
    evidence_ids = set(assistant_ids[-EVIDENCE_LAST_K:])
    lines = []
    for m in messages:
        who = "用户" if m["role"] == "user" else "助手"
        content = (m.get("content") or "").strip()
        if content:
            lines.append(f"{who}：{content}")
        if m["id"] in evidence_ids:
            ev = _evidence_line(m)
            if ev:
                lines.append(ev)
    return "\n".join(lines)
