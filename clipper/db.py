"""Base SQLite : schéma et petites fonctions d'accès.

Les fichiers lourds (vidéos, transcriptions, rendus) restent sur disque ;
la base ne garde que l'état et les chemins.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    added_at TEXT NOT NULL
);

-- Une ligne par vidéo source. `stage` = dernière étape réussie.
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL UNIQUE,      -- identifiant YouTube, ou "local-<hash>" pour un fichier
    url TEXT NOT NULL,
    local_path TEXT,                     -- chemin du fichier pour une source locale
    title TEXT,
    channel TEXT,
    duration REAL,
    upload_date TEXT,
    metadata TEXT,                       -- JSON (description, tags, etc.)
    stage TEXT NOT NULL DEFAULT 'new',   -- new, downloaded, transcribed, selected, ready
    error TEXT,                          -- message de la dernière erreur (NULL si OK)
    attempts INTEGER NOT NULL DEFAULT 0, -- nombre d'échecs consécutifs
    added_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY,
    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    raw_start REAL NOT NULL,             -- bornes proposées par Claude
    raw_end REAL NOT NULL,
    start REAL NOT NULL,                 -- bornes retenues (recalées puis ajustées en revue)
    end REAL NOT NULL,
    topic TEXT NOT NULL,
    score INTEGER NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'candidate',  -- candidate, auto_rejected, approved, rejected, rendered, uploaded
    reject_reason TEXT,
    titles TEXT,                         -- JSON : 3 propositions
    title TEXT,                          -- titre retenu
    description TEXT,
    tags TEXT,                           -- JSON
    thumb_text TEXT,
    thumb_frame INTEGER,
    thumb_path TEXT,
    render_path TEXT,
    rendered_start REAL,                 -- bornes du rendu existant (pour savoir s'il faut le refaire)
    rendered_end REAL,
    approved_at TEXT,
    yt_video_id TEXT UNIQUE,
    publish_at TEXT,
    uploaded_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS clips_video ON clips(video_id);
CREATE INDEX IF NOT EXISTS clips_status ON clips(status);

CREATE TABLE IF NOT EXISTS costs (
    id INTEGER PRIMARY KEY,
    video_id INTEGER REFERENCES videos(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,                  -- transcription, selection, titles
    detail TEXT,
    usd REAL NOT NULL,
    created_at TEXT NOT NULL
);

-- Unités de quota YouTube consommées, par jour (heure du Pacifique).
CREATE TABLE IF NOT EXISTS quota_usage (
    day TEXT PRIMARY KEY,
    units INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stats (
    clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    day_offset INTEGER NOT NULL,         -- 2 ou 7 jours après publication
    views INTEGER,
    avg_view_duration REAL,              -- secondes
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (clip_id, day_offset)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str) -> sqlite3.Connection:
    """Ouvre (et crée si besoin) la base. `:memory:` est accepté pour les tests."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)  # autocommit ; transactions explicites
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")  # lecture par l'interface pendant que `run` écrit
    conn.executescript(SCHEMA)
    return conn


class transaction:
    """Contexte `with transaction(conn):` → BEGIN / COMMIT, ROLLBACK en cas d'exception."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


# --- Vidéos -----------------------------------------------------------------

def add_video(conn, source_id: str, url: str, title: str | None = None,
              duration: float | None = None, local_path: str | None = None) -> bool:
    """Insère une vidéo si elle est inconnue. Renvoie True si elle a été ajoutée."""
    ts = now_iso()
    cur = conn.execute(
        """INSERT OR IGNORE INTO videos (source_id, url, local_path, title, duration, added_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (source_id, url, local_path, title, duration, ts, ts),
    )
    return cur.rowcount == 1


def get_video(conn, video_id: int):
    return conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()


def find_video(conn, ref: str):
    """Retrouve une vidéo par id numérique ou par source_id."""
    if ref.isdigit():
        row = get_video(conn, int(ref))
        if row:
            return row
    return conn.execute("SELECT * FROM videos WHERE source_id = ?", (ref,)).fetchone()


def update_video(conn, video_id: int, **fields) -> None:
    if "metadata" in fields and not isinstance(fields["metadata"], (str, type(None))):
        fields["metadata"] = json.dumps(fields["metadata"], ensure_ascii=False)
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE videos SET {cols} WHERE id = ?", (*fields.values(), video_id))


# --- Clips ------------------------------------------------------------------

JSON_CLIP_FIELDS = ("titles", "tags")


def update_clip(conn, clip_id: int, **fields) -> None:
    for key in JSON_CLIP_FIELDS:
        if key in fields and not isinstance(fields[key], (str, type(None))):
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE clips SET {cols} WHERE id = ?", (*fields.values(), clip_id))


def clip_dict(row) -> dict:
    """Ligne SQL → dict avec les champs JSON décodés."""
    d = dict(row)
    for key in JSON_CLIP_FIELDS:
        d[key] = json.loads(d[key]) if d.get(key) else []
    return d
