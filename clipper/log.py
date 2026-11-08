"""Journalisation en français, dans la console et dans data/clipper.log."""
from __future__ import annotations

import logging
from pathlib import Path

NIVEAUX = {"DEBUG": "DÉBOGAGE", "INFO": "INFO", "WARNING": "ATTENTION", "ERROR": "ERREUR", "CRITICAL": "CRITIQUE"}


class _FrenchFormatter(logging.Formatter):
    def format(self, record):
        record.niveau = NIVEAUX.get(record.levelname, record.levelname)
        return super().format(record)


def setup(log_dir: Path | None = None, verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger("clipper")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(_FrenchFormatter("%(asctime)s %(niveau)s %(message)s", "%H:%M:%S"))
    logger.addHandler(console)
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / "clipper.log", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(_FrenchFormatter("%(asctime)s %(niveau)s [%(module)s] %(message)s"))
        logger.addHandler(fh)
    return logger


log = logging.getLogger("clipper")
