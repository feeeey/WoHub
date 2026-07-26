"""单个截图 job 的子进程入口。

worker 把每个 job 放进独立进程执行：同步 Playwright 无法从线程安全中断，
而它存在不受 action/navigation 超时管辖的调用（如 download.save_as 等待
下载流完成）——线程内超时防不住所有挂死路径，进程级 SIGKILL 可以。

用法: python capture_worker.py <out_file>
      stdin 传 JSON {"symbol": ..., "timeframes": [...]}
      结果写 out_file: {"paths": [...]} 或 {"error": "..."}
stdout/stderr 直接继承（日志照常进 docker logs），结果走文件避免混流。
"""
import json
import sys


def main() -> int:
    out_path = sys.argv[1]
    payload = json.load(sys.stdin)
    try:
        from screenshot import capture_chart
        paths = capture_chart(payload["symbol"], payload["timeframes"])
        result = {"paths": paths}
    except Exception as e:
        result = {"error": str(e)[:500]}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
