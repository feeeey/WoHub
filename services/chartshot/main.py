from flask import Flask, jsonify

app = Flask(__name__)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "chartshot"})


@app.route("/api/screenshot", methods=["POST"])
def screenshot():
    # Stub — full implementation in Phase 1c
    return jsonify({"error": "not implemented"}), 501


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
