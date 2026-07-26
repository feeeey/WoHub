"""评测 CLI。在 backend/ 目录下运行：

  python -m evals                    # 离线：存量轨迹按 prompt_version×model 分桶报告
  python -m evals --live             # 金标实跑（真实 LLM 调用，工具数据用 fixtures 固定）
  python -m evals --live --case ID   # 只跑指定用例
  python -m evals --json out.json    # 附带 JSON 输出
  python -m evals extract            # 从存量轨迹提取用例骨架到 golden_extracted/
"""
import argparse
import json
import sys
from pathlib import Path


def _cmd_offline(args):
    from evals import report, runner
    rows = runner.score_stored(limit=args.limit)
    if not rows:
        print("chat_messages 里没有带轨迹的 assistant 消息，先用 agent 聊几轮再来。")
        return 0
    summary = report.summarize_stored(rows)
    print(report.render_stored_markdown(summary, len(rows)))
    if args.json:
        Path(args.json).write_text(
            json.dumps({"rows": rows,
                        "summary": {f"{k[0]}|{k[1]}": v for k, v in summary.items()}},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 已写入 {args.json}")
    return 0


def _cmd_live(args):
    from agent.config import load_config
    from agent.llm import build_model
    from agent.chat.prompts import CHAT_PROMPT_VERSION
    from evals import report, runner

    cfg_db = load_config()
    if not cfg_db.main_channel or not cfg_db.main_channel.api_key:
        print("未配置 LLM 渠道，无法实跑。到 Settings 页配置后重试。", file=sys.stderr)
        return 2
    model = build_model(cfg_db.main_channel, cfg_db.model)
    cfg = runner.EvalConfig(max_tool_calls=cfg_db.max_tool_calls,
                            deep_dive_limit=cfg_db.deep_dive_limit)
    case_ids = args.case or None
    results = runner.run_golden(model, cfg, case_ids=case_ids)
    print(report.render_golden_markdown(results, CHAT_PROMPT_VERSION, cfg_db.model))
    if args.json:
        Path(args.json).write_text(
            json.dumps({"prompt_version": CHAT_PROMPT_VERSION, "model": cfg_db.model,
                        "summary": report.summarize_golden(results),
                        "results": report.results_to_json(results)},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 已写入 {args.json}")
    return 0


def _cmd_extract(args):
    """从存量轨迹生成金标用例骨架（必须人工审校后才能进 golden/）。"""
    from config import settings
    from database import get_db

    db = get_db(settings.db_path)
    try:
        rows = db.execute(
            """SELECT a.id, a.trace_json, a.session_id,
                      (SELECT content FROM chat_messages u
                       WHERE u.session_id = a.session_id AND u.role = 'user'
                         AND u.id < a.id ORDER BY u.id DESC LIMIT 1) AS question
               FROM chat_messages a
               WHERE a.role = 'assistant' AND a.trace_json IS NOT NULL
               ORDER BY a.id DESC LIMIT ?""", (args.limit,)).fetchall()
    finally:
        db.close()

    out_dir = Path(__file__).parent / "golden_extracted"
    out_dir.mkdir(exist_ok=True)
    skeletons = []
    for r in rows:
        if not r["question"]:
            continue
        try:
            steps = json.loads(r["trace_json"]).get("steps") or []
        except json.JSONDecodeError:
            continue
        tools = [s["tool"] for s in steps if s.get("tool")]
        skeletons.append({"id": f"extracted-{r['id']}",
                          "question": r["question"][:200],
                          "must_call": sorted(set(tools)),
                          "answer_rules": ["nonempty_conclusion", "cn_language",
                                           "no_execution_claim"],
                          "notes": "自动提取骨架：must_call 是实际调用记录，"
                                   "需人工判断哪些是必要约束"})
    path = out_dir / "extracted.json"
    path.write_text(json.dumps(skeletons, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"提取 {len(skeletons)} 条骨架 → {path}（审校后移入 golden/）")
    return 0


def main():
    ap = argparse.ArgumentParser(prog="python -m evals", description=__doc__)
    ap.add_argument("command", nargs="?", default="report",
                    choices=["report", "extract"])
    ap.add_argument("--live", action="store_true", help="金标实跑（产生真实 LLM 费用）")
    ap.add_argument("--case", action="append", help="只跑指定用例 id（可重复）")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--json", help="同时写 JSON 到该路径")
    args = ap.parse_args()

    if args.command == "extract":
        return _cmd_extract(args)
    if args.live:
        return _cmd_live(args)
    return _cmd_offline(args)


if __name__ == "__main__":
    sys.exit(main())
