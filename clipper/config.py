"""Chargement de config.yaml et du fichier .env."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

# Valeurs par défaut : config.yaml ne fait que les surcharger.
DEFAULTS: dict = {
    "paths": {"data": "data", "prompts": "prompts"},
    "ingest": {
        "max_height": 1080,
        "min_duration_s": 600,
        "cookies_file": None,
        "cookies_from_browser": None,
        "js_runtimes": ["deno", "node"],
    },
    "transcription": {"provider": "deepgram", "model": "nova-3", "language": "fr"},
    "llm": {
        "model": "claude-sonnet-5-5",
        "fallback": True,
        "selection_effort": "high",
        "titles_effort": "medium",
    },
    "selection": {
        "min_clips": 5,
        "max_clips": 15,
        "max_overlap": 0.20,
        "block_minutes": 90,
        "block_overlap_minutes": 5,
    },
    "bounds": {"min_duration_s": 120, "max_duration_s": 300, "margin_s": 0.3, "snap_window_s": 15},
    "render": {
        "width": 1920, "height": 1080, "crf": 18, "preset": "medium", "audio_bitrate": "192k",
        "fade_s": 0.3, "loudness_lufs": -14, "true_peak_db": -1.0,
    },
    "thumbnail": {
        "frames": 10, "width": 1280, "height": 720, "max_bytes": 2_000_000,
        "font_path": None, "font_size": 110, "text_color": "#FFFFFF", "stroke_color": "#000000",
        "stroke_width": 8, "position": "bottom", "margin": 50, "uppercase": True,
        "band_color": "#00000099",
    },
    "publish": {
        "timezone": "Europe/Paris", "slots": ["18:00"],
        "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        "playlist_id": None, "category_id": "27", "default_language": "fr", "min_lead_hours": 2,
    },
    "quota": {
        "daily_limit": 10000, "upload_cost": 1600, "thumbnail_cost": 50,
        "playlist_cost": 50, "list_cost": 1,
    },
    "stats": {"checkpoints_days": [2, 7]},
    "review": {"host": "127.0.0.1", "port": 8765},
    "pricing": {
        "deepgram_per_min": 0.0043,
        "deepgram_diarize_per_min": 0.0020,
        "anthropic": {
            "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5},
        },
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_env(path: Path) -> None:
    """Lit un .env minimal (CLE=valeur). N'écrase pas les variables déjà définies."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


class Config(dict):
    """Dictionnaire de configuration + chemins absolus résolus depuis la racine du projet."""

    root: Path

    @property
    def data_dir(self) -> Path:
        return (self.root / self["paths"]["data"]).resolve()

    @property
    def prompts_dir(self) -> Path:
        return (self.root / self["paths"]["prompts"]).resolve()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "clipper.db"

    def video_dir(self, source_id: str) -> Path:
        return self.data_dir / "videos" / source_id

    def clip_dir(self, clip_id: int) -> Path:
        return self.data_dir / "clips" / str(clip_id)


def load_config(root: Path | None = None) -> Config:
    """Charge config.yaml et .env depuis `root` (par défaut : $CLIPPER_HOME ou le dossier courant)."""
    root = Path(root or os.environ.get("CLIPPER_HOME") or Path.cwd()).resolve()
    load_env(root / ".env")
    user_cfg = {}
    cfg_file = root / "config.yaml"
    if cfg_file.exists():
        user_cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
    cfg = Config(_deep_merge(DEFAULTS, user_cfg))
    cfg.root = root
    return cfg


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Variable {name} absente : renseigne-la dans .env (voir .env.example).")
    return value
