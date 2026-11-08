"""Point d'entrée `clipper` : add, run, review, publish, status, stats."""
from __future__ import annotations

import argparse
import sys

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import costs, db, quota
from . import publish as pub
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
            "selected": "sélectionnée", "bounded": "bornes calées", "titled": "titrée",
            "ready": "prête pour la revue"}


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
    print(f"File de publication : {len(pub.queue(conn))} clip(s) validé(s) en attente.")
    print(_quota_line(conn, cfg))
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


def cmd_review(conn, cfg, args) -> int:
    import threading
    import webbrowser

    import uvicorn

    from .web.app import create_app

    host, port = cfg["review"]["host"], args.port or cfg["review"]["port"]
    url = f"http://{host}:{port}/"
    ready = conn.execute("SELECT COUNT(*) FROM videos WHERE stage = 'ready'").fetchone()[0]
    if not ready:
        log.warning("Aucune vidéo prête pour la revue : lance d'abord `clipper run`.")
    log.info("Page de revue : %s (Ctrl+C pour arrêter)", url)
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")
    return 0


def _quota_line(conn, cfg) -> str:
    reset = quota.next_reset().astimezone(ZoneInfo(cfg["publish"]["timezone"]))
    return (f"Quota YouTube : {quota.remaining(conn, cfg)} / {cfg['quota']['daily_limit']} unités restantes "
            f"aujourd'hui → {quota.clips_left_today(conn, cfg)} clip(s) publiable(s) "
            f"(remise à zéro {reset:%d/%m à %H:%M})")


def cmd_publish(conn, cfg, args) -> int:
    pending = pub.queue(conn)
    print(_quota_line(conn, cfg))
    print(f"File de publication : {len(pending)} clip(s) validé(s) en attente.")
    if args.dry_run:
        for clip, slot in pub.plan(conn, cfg, limit=args.max):
            print(f"  #{clip['id']:<4} {pub.fmt_local(slot, cfg)}  {clip['title']}")
        rest = len(pending) - len(pub.plan(conn, cfg, limit=args.max))
        if rest > 0:
            print(f"  … {rest} autre(s) clip(s) restent dans la file pour les jours suivants.")
        return 0
    if not pending and not pub.unfinished(conn) and not args.login:
        print("Rien à publier. Valide des clips avec `clipper review`.")
        return 0

    from .youtube import YouTube, get_credentials

    yt = YouTube(get_credentials(cfg, force_login=args.login, open_browser=not args.no_browser))
    if args.login:
        quota.charge(conn, cfg["quota"]["list_cost"])
        ch = yt.channel()
        log.info("Connecté à la chaîne « %s » (%s).", ch["title"], ch["id"])
        if not pending:
            return 0
    report = pub.publish(conn, cfg, yt, limit=args.max)
    for cid, yt_id, at in report.uploaded:
        log.info("✓ #%d → https://youtu.be/%s (privée, publication programmée %s)", cid, yt_id,
                 pub.fmt_local(pub.parse_iso(at), cfg))
    for cid, why in report.skipped:
        log.warning("Clip #%d : %s", cid, why)
    if report.stopped_reason:
        left = len(pub.queue(conn))
        log.warning("Arrêt : %s. %d clip(s) restent dans la file, relance `clipper publish` après la remise à zéro.",
                    report.stopped_reason, left)
    print(_quota_line(conn, cfg))
    return 0


def cmd_stats(conn, cfg, args) -> int:
    from pathlib import Path

    from . import stats

    if not args.no_fetch and stats.due_checkpoints(conn, cfg, datetime.now(timezone.utc)):
        from .youtube import YouTube, get_credentials

        stats.collect(conn, cfg, YouTube(get_credentials(cfg, open_browser=not args.no_browser)))
    rows = stats.table(conn, cfg)
    if not rows:
        print("Aucun clip publié pour l'instant.")
        return 0
    days = cfg["stats"]["checkpoints_days"]
    head = "".join(f" {'J+' + str(d) + ' vues':>10} {'moy.':>6} {'rét.':>6}" for d in days)
    print(f"{'#':>4} {'Note':>4}  {'Publié':<11} {'Titre':<44}{head}")
    fmt = lambda v, suffix="": "–" if v is None else f"{v}{suffix}"  # noqa: E731
    for r in rows:
        when = pub.fmt_local(pub.parse_iso(r["publie_le"]), cfg)[5:] if r["publie_le"] else "?"
        cells = "".join(f" {fmt(r[f'vues_j{d}']):>10} {fmt(r[f'duree_moy_j{d}_s'], 's'):>6} "
                        f"{fmt(r[f'retention_j{d}_pct'], '%'):>6}" for d in days)
        print(f"{r['clip_id']:>4} {r['note']:>4}  {when:<11} {(r['titre'] or '')[:44]:<44}{cells}")
    if args.csv:
        path = stats.export_csv(rows, Path(args.csv))
        log.info("Export CSV : %s (%d clip(s))", path, len(rows))
    return 0


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

    rv = sub.add_parser("review", help="ouvrir la page de revue locale")
    rv.add_argument("--port", type=int, help="port (par défaut : review.port de config.yaml)")
    rv.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur")
    rv.set_defaults(func=cmd_review)
    pb = sub.add_parser("publish", help="envoyer les clips validés (privés, publication programmée)")
    pb.add_argument("--dry-run", action="store_true", help="afficher les créneaux prévus sans rien envoyer")
    pb.add_argument("--max", type=int, help="nombre maximum de clips à envoyer")
    pb.add_argument("--login", action="store_true", help="se (re)connecter à YouTube, par ex. pour changer de chaîne")
    pb.add_argument("--no-browser", action="store_true", help="afficher l'adresse de connexion sans ouvrir le navigateur")
    pb.set_defaults(func=cmd_publish)
    st = sub.add_parser("stats", help="relever vues et durée moyenne de visionnage à J+2 et J+7")
    st.add_argument("--csv", metavar="FICHIER", help="exporter le tableau en CSV (ex. stats.csv)")
    st.add_argument("--no-fetch", action="store_true", help="afficher sans interroger YouTube")
    st.add_argument("--no-browser", action="store_true", help="afficher l'adresse de connexion sans ouvrir le navigateur")
    st.set_defaults(func=cmd_stats)
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
