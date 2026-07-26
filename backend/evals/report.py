"""评测结果聚合与渲染：按 prompt_version × model 分桶，输出 markdown/JSON。"""
from collections import defaultdict
from dataclasses import asdict
from statistics import mean


def summarize_stored(rows: list[dict]) -> dict:
    """离线报告：{(prompt_version, model): 聚合指标}。"""
    buckets = defaultdict(list)
    for r in rows:
        buckets[(r["prompt_version"], r["model"])].append(r)
    out = {}
    for key, items in buckets.items():
        rule_fail = defaultdict(int)
        for it in items:
            for name, ok in it["rules"].items():
                if ok is False:
                    rule_fail[name] += 1
        out[key] = {
            "n": len(items),
            "avg_tool_calls": round(mean(i["n_calls"] for i in items), 2),
            "total_repeats": sum(i["repeats"] for i in items),
            "total_tool_errors": sum(i["errors"] for i in items),
            "avg_l2": round(mean(i["l2"] for i in items), 4),
            "avg_l3": round(mean(i["l3"] for i in items if i["l3"] is not None), 4)
                      if any(i["l3"] is not None for i in items) else None,
            "rule_failures": dict(rule_fail),
        }
    return out


def render_stored_markdown(summary: dict, total_rows: int) -> str:
    lines = [f"# 存量轨迹评测（{total_rows} 条 assistant 轨迹）", "",
             "| prompt_version | model | n | 均工具调用 | 重复 | 工具报错 | L2 | L3 | 规则违例 |",
             "|---|---|--:|--:|--:|--:|--:|--:|---|"]
    for (pv, model), s in sorted(summary.items()):
        fails = "; ".join(f"{k}×{v}" for k, v in s["rule_failures"].items()) or "-"
        lines.append(f"| {pv} | {model} | {s['n']} | {s['avg_tool_calls']} "
                     f"| {s['total_repeats']} | {s['total_tool_errors']} "
                     f"| {s['avg_l2']} | {s['avg_l3']} | {fails} |")
    lines.append("")
    lines.append("L2=轨迹效率 L3=答案质量（缺省规则集）。离线模式无 L1——存量流量没有期望约束。")
    return "\n".join(lines)


def summarize_golden(results: list) -> dict:
    l1s = [r.l1["score"] for r in results if r.l1.get("score") is not None]
    l3s = [r.l3["score"] for r in results if r.l3.get("score") is not None]
    return {"n_cases": len(results),
            "avg_total": round(mean(r.total for r in results), 4) if results else None,
            "avg_l1": round(mean(l1s), 4) if l1s else None,
            "avg_l2": round(mean(r.l2["score"] for r in results), 4) if results else None,
            "avg_l3": round(mean(l3s), 4) if l3s else None,
            "failed_cases": [r.case_id for r in results if r.total < 0.7]}


def render_golden_markdown(results: list, prompt_version: str, model: str) -> str:
    s = summarize_golden(results)
    lines = [f"# 金标评测  prompt={prompt_version}  model={model}", "",
             f"用例 {s['n_cases']} 条 | 总分 {s['avg_total']} | "
             f"L1 {s['avg_l1']} | L2 {s['avg_l2']} | L3 {s['avg_l3']}", "",
             "| 用例 | 总分 | L1 | L2 | L3 | 调用数 | 问题点 |",
             "|---|--:|--:|--:|--:|--:|---|"]
    for r in results:
        issues = list(r.l1.get("violations") or [])
        issues += [f"重复调用×{r.l2['repeats']}"] if r.l2.get("repeats") else []
        issues += [f"规则失败:{n}" for n, ok in (r.l3.get("rules") or {}).items()
                   if ok is False]
        lines.append(f"| {r.case_id} | {r.total} | {r.l1.get('score')} "
                     f"| {r.l2.get('score')} | {r.l3.get('score')} "
                     f"| {r.n_steps} | {'; '.join(issues) or '-'} |")
    return "\n".join(lines)


def results_to_json(results: list) -> list[dict]:
    return [asdict(r) for r in results]
