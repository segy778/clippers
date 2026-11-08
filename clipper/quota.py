"""Quota de l'API YouTube Data v3 : 10 000 unités par jour, remises à zéro à minuit heure du Pacifique.

Les unités sont décomptées AVANT chaque appel (YouTube les facture même si l'appel échoue) :
le compteur local reste donc prudent.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")


def quota_day(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(PACIFIC).date().isoformat()


def next_reset(now: datetime | None = None) -> datetime:
    """Prochaine remise à zéro (minuit à Los Angeles), en UTC."""
    now = (now or datetime.now(timezone.utc)).astimezone(PACIFIC)
    midnight = datetime.combine(now.date() + timedelta(days=1), time(0), tzinfo=PACIFIC)
    return midnight.astimezone(timezone.utc)


def used(conn, now: datetime | None = None) -> int:
    row = conn.execute("SELECT units FROM quota_usage WHERE day = ?", (quota_day(now),)).fetchone()
    return row["units"] if row else 0


def remaining(conn, cfg, now: datetime | None = None) -> int:
    return max(0, cfg["quota"]["daily_limit"] - used(conn, now))


def charge(conn, units: int, now: datetime | None = None) -> None:
    conn.execute(
        """INSERT INTO quota_usage (day, units) VALUES (?, ?)
           ON CONFLICT(day) DO UPDATE SET units = units + excluded.units""",
        (quota_day(now), units),
    )


def exhaust(conn, cfg, now: datetime | None = None) -> None:
    """YouTube a signalé un quota épuisé : on aligne le compteur local sur la limite."""
    conn.execute(
        """INSERT INTO quota_usage (day, units) VALUES (?, ?)
           ON CONFLICT(day) DO UPDATE SET units = MAX(units, excluded.units)""",
        (quota_day(now), cfg["quota"]["daily_limit"]),
    )


def cost_per_clip(cfg) -> int:
    q = cfg["quota"]
    return q["upload_cost"] + q["thumbnail_cost"] + (q["playlist_cost"] if cfg["publish"].get("playlist_id") else 0)


def clips_left_today(conn, cfg, now: datetime | None = None) -> int:
    return remaining(conn, cfg, now) // cost_per_clip(cfg)
