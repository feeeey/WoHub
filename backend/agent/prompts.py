PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """你是加密永续合约的技术分析决策员，对一次筛选任务产出的候选信号逐个裁决。

硬约束：
- 纯技术分析：只依据价格、成交量、衍生指标与市场结构；不引入任何消息面/情绪/链上判断。
- 你只做研究裁决（direction/confidence/理由），不下单、不算仓位——执行与风险由确定性系统负责。
- 工具预算有限（深评有配额）；先用批内证据（重叠计数、跨周期共振、快照）粗排，只对最值得的候选调 kline_summary 深评。
- 筛选结果为空可能是"无信号"也可能是"数据源失败"，不要过度解读空集。
- K线形态的方向标签是启发式，不是既定事实。

对每个候选输出：direction（long/short/skip）、confidence（0-1，校准而非乐观）、reasons（中文，简洁、可复核）、factors（可选的关键数值证据）。没有把握就 skip——skip 是合法且常见的正确答案。"""


def render_batch(context: dict) -> str:
    """把 context_json（SignalBatch 快照）紧凑渲染为用户消息。
    对缺键容错：队列里的 context 可能因版本演进缺字段。

    注意：labels/symbol 等字符串来自运营者控制的筛选器配置而非终端用户自由文本，
    注入面可接受。
    """
    task_id = context.get("task_id", "?")
    task_type = context.get("task_type", "?")
    lines = [f"任务 #{task_id}（{task_type}）本轮筛选批："]
    for r in context.get("results", []):
        note = "（空集：无信号或数据源失败）" if not r.get("symbols") else ""
        lines.append(f"- {r.get('label', '?')} @{r.get('resolution', '?')}: {len(r.get('symbols', []))} 命中{note}")
    lines.append("\n候选信号（已通过规则阈值并落库，请逐个裁决；其余符号仅作背景）：")
    for c in context.get("candidates", []):
        snap = c.get("snapshot") or {}
        lines.append(f"- {c.get('symbol', '?')} @{c.get('timeframe', '?')} ← {'、'.join(c.get('labels', []))}"
                     f"｜24h涨跌 {snap.get('priceChangePercent', '?')}%"
                     f"｜资金费率 {snap.get('fundingRate', '?')}")
    cross = context.get("cross") or {}
    if cross.get("resolution_overlap"):
        lines.append(f"\n跨周期共振: {cross['resolution_overlap']}")
    if cross.get("full_overlap"):
        lines.append(f"全交集: {cross['full_overlap']}")
    if context.get("bias_map"):
        lines.append(f"筛选器方向语义: {context['bias_map']}")
    return "\n".join(lines)
