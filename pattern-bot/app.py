"""
Validation UI for Gold history + pattern explorer.

  py app.py
  → http://127.0.0.1:5055/
"""
from __future__ import annotations

from flask import Flask, jsonify, render_template, request

import config
from history_data import load_candles, summarize_file
from pattern_engine import analyze_recent, candles_for_match

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("validate.html")


@app.route("/api/history/summary")
def api_summary():
    try:
        return jsonify(summarize_file())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/history/candles")
def api_candles():
    try:
        start_ms = request.args.get("start_ms", type=int)
        end_ms = request.args.get("end_ms", type=int)
        limit = request.args.get("limit", default=1500, type=int)
        candles = load_candles(
            start_ms=start_ms, end_ms=end_ms, limit=min(limit, 20000), tail=True
        )
        return jsonify({"ok": True, "n": len(candles), "candles": candles})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/history/patterns")
def api_patterns():
    try:
        lookback = request.args.get("lookback", default=config.PATTERN_LOOKBACK, type=int)
        horizon = request.args.get("horizon", default=config.PATTERN_HORIZON, type=int)
        top_k = request.args.get("top_k", default=config.PATTERN_TOP_K, type=int)
        min_score = request.args.get("min_score", default=config.PATTERN_MIN_SCORE, type=float)
        return jsonify(
            analyze_recent(
                lookback=lookback, horizon=horizon, top_k=top_k, min_score=min_score
            )
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/history/match_window")
def api_match_window():
    try:
        start_idx = request.args.get("start_idx", type=int)
        end_idx = request.args.get("end_idx", type=int)
        horizon = request.args.get("horizon", default=30, type=int)
        if start_idx is None or end_idx is None:
            return jsonify({"ok": False, "error": "start_idx and end_idx required"})
        data = candles_for_match(start_idx, end_idx, horizon=horizon)
        return jsonify({"ok": True, **data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


if __name__ == "__main__":
    print("=" * 56)
    print("  Pattern Bot · History Validate UI")
    print(f"  http://{config.UI_HOST}:{config.UI_PORT}/")
    print("  First: py download_history.py  (needs Node.js)")
    print("=" * 56)
    app.run(host=config.UI_HOST, port=config.UI_PORT, debug=False)
