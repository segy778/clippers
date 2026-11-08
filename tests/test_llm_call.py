"""Appel réel du SDK anthropic contre un faux serveur local (SSE) : forme de la requête, coût, JSON."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from clipper.llm import Claude, LLMError

CAPTURED = {}


def _sse(stop_reason="end_turn", text='{"candidates": []}'):
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 1000, "output_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                           "usage": {"output_tokens": 500}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


class Handler(BaseHTTPRequestHandler):
    reply = _sse()

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        CAPTURED["body"] = json.loads(body)
        CAPTURED["headers"] = dict(self.headers)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(Handler.reply)

    def log_message(self, *a):
        pass


@pytest.fixture
def server(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{srv.server_port}")
    yield
    srv.shutdown()


def test_json_call_request_shape_and_cost(server, conn, cfg):
    Handler.reply = _sse()
    out = Claude(cfg, conn).json_call(system="sys", user="transcription", schema={"type": "object"},
                                      effort="high", video_id=None, kind="selection")
    assert out == {"candidates": []}
    body = CAPTURED["body"]
    assert body["model"] == "claude-sonnet-5-5" and body["stream"] is True
    assert body["fallbacks"] == "default"
    assert body["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert "server-side-fallback-2026-07-01" in CAPTURED["headers"].get("anthropic-beta", "")
    usd = conn.execute("SELECT usd FROM costs").fetchone()[0]
    assert usd == pytest.approx((1000 * 2 + 500 * 10) / 1e6)


def test_json_call_refusal(server, conn, cfg):
    Handler.reply = _sse(stop_reason="refusal", text="")
    with pytest.raises(LLMError):
        Claude(cfg, conn).json_call(system="s", user="u", schema={}, effort="low", video_id=None, kind="x")
