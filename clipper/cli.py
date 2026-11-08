"""Point d'entrée `clipper` : add, run, review, publish, status, stats."""
from __future__ import annotations

import argparse
import sys

from . import costs, db
from .config import load_config
from .log import log, setup
from .transcript import fmt_ts


def cmd_add(conn, cfg, args) -> int:
    from .ingest import expand

    total_new = 0
    for url in args.urls:
        try:
            items = expand(url, cfg)
        except Exception as exc:
            log.error("Impossible de lire %s : %s", url, exc)
            continue
        conn.execute("INSERT OR IGNORE INTO sources (url, added_at) VALUES (?, ?)", (url, db.now_iso()))
        new = sum(db.add_video(conn, v["source_id"], v["url"], v["title"], v["duration"], v["local_path"])
                  for v in items)
        total_new += new
        log.info("%s : %d vidéo(s) trouvée(s), %d nouvelle(s), %d déjà connue(s).",
                 url, len(items), new, len(items) - new)
    if total_new:
        log.info("Lance `clipper run` pour les traiter.")
    return 0


def cmd_run(conn, cfg, args) -> int:
    from .pipeline import run

    only = None
    if args.video:
        row = db.find_video(conn, args.video)
        if not row:
            log.error("Vidéo inconnue : %s", args.video)
            return 1
        only = [row["id"]]
    _, ko = run(conn, cfg, retry=args.retry, only=only)
    return 1 if ko else 0


STAGE_FR = {"new": "à télécharger", "downloaded": "téléchargée", "transcribed": "transcrite",
            "selected": "candidats prêts", "ready": "prête pour la revue"}


def cmd_status(conn, cfg, args) -> int:
    if args.video:
        return _status_video(conn, args.video)
    rows = conn.execute("SELECT * FROM videos ORDER BY id").fetchall()
    if not rows:
        print("Aucune vidéo. Ajoute-en avec `clipper add URL`.")
        return 0
    counts = {
        r["video_id"]: r for r in conn.execute(
            """SELECT video_id, COUNT(*) AS n,
                      SUM(status IN ('approved','rendered','uploaded')) AS ok,
                      SUM(status = 'uploaded') AS up
               FROM clips GROUP BY video_id""")
    }
    print(f"{'ID':>3}  {'Vidéo':<44} {'Durée':>7}  {'État':<20} {'Clips':>5} {'Valid.':>6} {'Publ.':>5} {'Coût':>8}")
    for r in rows:
        c = counts.get(r["id"])
        state = STAGE_FR.get(r["stage"], r["stage"])
        if r["error"]:
            state = f"ÉCHEC ×{r['attempts']}"
        title = (r["title"] or r["url"])[:44]
        dur = fmt_ts(r["duration"]) if r["duration"] else "?"
        print(f"{r['id']:>3}  {title:<44} {dur:>7}  {state:<20} {c['n'] if c else 0:>5} "
              f"{(c['ok'] or 0) if c else 0:>6} {(c['up'] or 0) if c else 0:>5} {costs.video_cost(conn, r['id']):>7.3f}$")
    print(f"\nCoût cumulé : {costs.total_cost(conn):.3f} $")
    errors = [r for r in rows if r["error"]]
    for r in errors:
        print(f"  ✗ {r['id']} [{r['source_id']}] {r['error'][:150]}")
    return 0


def _status_video(conn, ref: str) -> int:
    v = db.find_video(conn, ref)
    if not v:
        log.error("Vidéo inconnue : %s", ref)
        return 1
    print(f"{v['title']}  [{v['source_id']}]  {fmt_ts(v['duration'] or 0)}  état : {STAGE_FR.get(v['stage'], v['stage'])}")
    print(f"Coût : {costs.video_cost(conn, v['id']):.3f} $\n")
    clips = conn.execute("SELECT * FROM clips WHERE video_id = ? ORDER BY start", (v["id"],)).fetchall()
    for c in clips:
        dur = c["end"] - c["start"]
        print(f"#{c['id']:<4} {c['score']:>3}/100  {fmt_ts(c['start'])} → {fmt_ts(c['end'])} ({fmt_ts(dur)})  [{c['status']}]")
        print(f"       {c['topic']}")
        if c["reason"]:
            print(f"       ↳ {c['reason']}")
        if c["reject_reason"]:
            print(f"       ✗ {c['reject_reason']}")
    return 0


def not_yet(phase: int):
    def handler(conn, cfg, args) -> int:
        log.warning("Commande disponible à la phase %d.", phase)
        return 1
    return handler


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clipper", description="Vidéos longues → clips YouTube de 2 à 5 min.")
    p.add_argument("-v", "--verbose", action="store_true", help="logs détaillés")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("add", help="ajouter une vidéo, une playlist, une chaîne ou un fichier local")
    a.add_argument("urls", nargs="+")
    a.set_defaults(func=cmd_add)

    r = sub.add_parser("run", help="traiter les vidéos en attente (reprend après un crash)")
    r.add_argument("--video", help="ne traiter que cette vidéo (id ou identifiant YouTube)")
    r.add_argument("--retry", action="store_true", help="réessayer aussi les vidéos en échec répété")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("status", help="état des vidéos, coûts et quota")
    s.add_argument("video", nargs="?", help="détail des clips d'une vidéo")
    s.set_defaults(func=cmd_status)

    sub.add_parser("review", help="ouvrir la page de revue locale").set_defaults(func=not_yet(2))
    sub.add_parser("publish", help="publier les clips validés selon la cadence").set_defaults(func=not_yet(3))
    sub.add_parser("stats", help="relever vues et durée moyenne de visionnage").set_defaults(func=not_yet(4))
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config()
    setup(cfg.data_dir, args.verbose)
    conn = db.connect(cfg.db_path)
    try:
        return args.func(conn, cfg, args) or 0
    except KeyboardInterrupt:
        return 130
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
