"""Estimation et enregistrement des coûts d'API (Deepgram, Anthropic)."""
from __future__ import annotations

from .db import now_iso


def record(conn, video_id: int | None, kind: str, usd: float, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO costs (video_id, kind, detail, usd, created_at) VALUES (?, ?, ?, ?, ?)",
        (video_id, kind, detail, round(usd, 6), now_iso()),
    )


def deepgram_cost(cfg, duration_s: float) -> float:
    p = cfg["pricing"]
    return duration_s / 60 * (p["deepgram_per_min"] + p["deepgram_diarize_per_min"])


def anthropic_cost(cfg, model: str, usage) -> float:
    """Coût d'une réponse à partir de son objet `usage` (tokens)."""
    prices = cfg["pricing"]["anthropic"].get(model)
    if not prices:
        return 0.0
    get = lambda name: getattr(usage, name, 0) or 0  # noqa: E731
    total = (
        get("input_tokens") * prices["input"]
        + get("output_tokens") * prices["output"]
        + get("cache_read_input_tokens") * prices.get("cache_read", prices["input"])
        + get("cache_creation_input_tokens") * prices.get("cache_write", prices["input"])
    )
    return total / 1_000_000


def video_cost(conn, video_id: int) -> float:
    row = conn.execute("SELECT COALESCE(SUM(usd), 0) FROM costs WHERE video_id = ?", (video_id,)).fetchone()
    return row[0]


def total_cost(conn) -> float:
    return conn.execute("SELECT COALESCE(SUM(usd), 0) FROM costs").fetchone()[0]
