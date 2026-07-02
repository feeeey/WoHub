import os, json
from unittest.mock import patch
from database import get_db
from agent.decider import SignalBatch, Decision


def _fixture():
    # task_id 用 None：FK 开启时非空 task_id 必须存在于 tasks 表，
    # 而 _enqueue_agent_run 的 try/except 会吞掉 IntegrityError，导致断言神秘失败
    batch = SignalBatch(task_id=None, task_type="watchlist_signal",
                        config={"resolutions": ["1h"]},
                        results=[{"label": "底背离", "resolution": "1h",
                                  "symbols": ["BINANCE:BTCUSDT.P"], "count": 1}],
                        bias_map={"底背离": "long"})
    decisions = [Decision(symbol="BINANCE:BTCUSDT.P", timeframe="1h", direction="long",
                          confidence=None, reasons="", labels=["底背离"])]
    return batch, decisions, {("BTCUSDT", "1h"): [42]}


def _count():
    db = get_db(os.environ["DB_PATH"])
    n = db.execute("SELECT COUNT(*) FROM agent_runs WHERE decider='agent'").fetchone()[0]
    db.close()
    return n


SNAP = {"BTCUSDT": {"lastPrice": 1.0, "priceChangePercent": 2.0, "volume24h": 1.0, "fundingRate": None}}


def test_enqueue_respects_enabled_and_action(reset_db):
    from tasks.executor import _enqueue_agent_run
    from agent.config import save_config
    batch, decisions, id_map = _fixture()

    with patch("agent.tools.market_snapshot", return_value=SNAP):
        # agent 未启用：不入队
        _enqueue_agent_run(None, batch, decisions, id_map, actions=["agent_decide"])
        assert _count() == 0

        save_config({"provider": "openai", "model": "m", "api_key": "k", "enabled": True,
                     "base_url": "", "max_tokens": 4096, "max_tool_calls": 15,
                     "deep_dive_limit": 5, "cooldown_minutes": 240, "push_verdict": False,
                     "credential_id": None})
        # action 不含 agent_decide：不入队
        _enqueue_agent_run(None, batch, decisions, id_map, actions=["text_summary"])
        assert _count() == 0
        # 启用 + action 命中：入队且 context 完整
        _enqueue_agent_run(None, batch, decisions, id_map, actions=["agent_decide"])
        assert _count() == 1

    db = get_db(os.environ["DB_PATH"])
    row = db.execute("SELECT * FROM agent_runs WHERE decider='agent'").fetchone()
    db.close()
    ctx = json.loads(row["context_json"])
    assert ctx["candidates"][0]["symbol"] == "BTCUSDT"
    assert ctx["candidates"][0]["signal_ids"] == [42]
    assert ctx["candidates"][0]["snapshot"]["priceChangePercent"] == 2.0


def test_market_scan_handler_enqueues(reset_db):
    """端到端：_exec_market_scan 在 agent_decide + enabled 时入队（第二调用点回归守护）。"""
    from unittest.mock import patch
    from tasks import executor
    from agent.config import save_config
    save_config({"provider": "openai", "model": "m", "api_key": "k", "enabled": True,
                 "base_url": "", "max_tokens": 4096, "max_tool_calls": 15,
                 "deep_dive_limit": 5, "cooldown_minutes": 240, "push_verdict": False,
                 "credential_id": None})
    config = {"screeners": [{"folder_type": "oscillator", "screener_name": "oversold_zone", "label": "超卖"},
                            {"folder_type": "oscillator", "screener_name": "divergence_bottom", "label": "底背离"}],
              "resolutions": ["1h"], "overlap_threshold": 2, "watchlist_id": 0}
    with patch.object(executor, "run_screener", return_value=["BINANCE:BTCUSDT.P"]), \
         patch.object(executor, "record_snapshot"), \
         patch.object(executor, "schedule_outcome_tracking"), \
         patch("agent.tools.market_snapshot", return_value={"BTCUSDT": {"lastPrice": 1.0,
               "priceChangePercent": 0.0, "volume24h": 1.0, "fundingRate": None}}):
        executor._exec_market_scan(None, config, ["agent_decide"], None)
    assert _count() == 1
