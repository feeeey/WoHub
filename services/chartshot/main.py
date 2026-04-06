import os
import uuid
from flask import Flask, jsonify, request, send_file
from config import HOST, PORT, OUTPUT_DIR, VALID_TIMEFRAMES
from worker import CaptureWorker, CaptureJob
from cookies_manager import (
    get_raw_cookie_string,
    save_cookies_from_string,
    test_validity,
)

app = Flask(__name__)
worker = CaptureWorker()


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "chartshot"})


@app.route("/api/screenshot", methods=["POST"])
def screenshot():
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "JSON body required"}), 400

    symbol = data.get("symbol", "").strip().upper()
    if not symbol:
        return jsonify({"ok": False, "error": "symbol is required"}), 400

    timeframes = data.get("timeframes", [])
    if not timeframes:
        return jsonify({"ok": False, "error": "timeframes is required"}), 400

    invalid = [tf for tf in timeframes if tf not in VALID_TIMEFRAMES]
    if invalid:
        return jsonify({"ok": False, "error": f"Invalid timeframes: {invalid}"}), 400

    job = CaptureJob(
        symbol=symbol,
        timeframes=timeframes,
        job_id=uuid.uuid4().hex[:8],
    )
    worker.submit(job)

    done = job.done_event.wait(timeout=120)
    if not done:
        return jsonify({"ok": False, "error": "Timeout waiting for screenshot"}), 504

    if job.error:
        return jsonify({"ok": False, "error": job.error}), 500

    files = []
    paths = []
    for p in (job.result or []):
        paths.append(p)
        files.append(os.path.basename(p))

    return jsonify({"ok": True, "files": files, "paths": paths})


@app.route("/api/screenshot/file/<filename>")
def get_screenshot_file(filename):
    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.isfile(filepath):
        return jsonify({"error": "File not found"}), 404
    return send_file(filepath, mimetype="image/png")


@app.route("/api/cookies", methods=["GET"])
def get_cookies():
    try:
        raw = get_raw_cookie_string()
        return jsonify({"ok": True, "cookies": raw})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/cookies", methods=["PUT"])
def update_cookies():
    data = request.get_json()
    raw = data.get("cookies", "") if data else ""
    if not raw:
        return jsonify({"ok": False, "error": "cookies string required"}), 400
    try:
        save_cookies_from_string(raw)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/cookies/test", methods=["POST"])
def test_cookies():
    result = test_validity()
    return jsonify(result)


if __name__ == "__main__":
    worker.start()
    try:
        app.run(host=HOST, port=PORT, debug=False)
    finally:
        worker.stop()
