# Clipper

## But
Transformer des vidéos YouTube longues (podcasts, interviews, formations — droits détenus par l'utilisateur) en clips horizontaux de 2 à 5 min, notés, titrés, avec miniature ; revue locale en un clic ; publication programmée en privé sur une chaîne de clips ; suivi des vues.

## Stack
- Python ≥ 3.11, CLI `argparse` (`clipper/cli.py`), SQLite stdlib (`data/clipper.db`).
- yt-dlp (API Python), Deepgram REST via httpx (nova-3, diarisation), SDK `anthropic` (Claude Sonnet 5.5 par défaut, sortie JSON contrainte via `output_config.format`, repli serveur `fallbacks="default"`), ffmpeg en sous-processus.
- Phases à venir : FastAPI + HTML/JS vanilla (revue), Pillow + OpenCV (miniatures), google-api-python-client (publication, stats).
- Clés dans `.env` (lu par `config.load_env`, pas de python-dotenv), réglages dans `config.yaml` (fusionnés sur `config.DEFAULTS`).

## Commandes
```bash
pip install -e ".[dev]"        # installation
python -m pytest -q            # tests
clipper add URL|fichier        # vidéo, playlist, chaîne ou fichier local
clipper run [--video ID] [--retry]
clipper status [ID]            # tableau + coûts ; avec ID : détail des clips
clipper review | publish | stats   # phases 2, 3, 4
```

## Architecture
- `pipeline.py` : machine à états par vidéo (`videos.stage` = dernière étape réussie : new → downloaded → transcribed → selected). Chaque étape vérifie son artefact avant d'agir. Une exception marque `error`/`attempts` et passe à la vidéo suivante (3 échecs max sauf `--retry`).
- `ingest.py` (yt-dlp, proxy 720p), `transcribe.py` (backend + cache `transcript.json`), `transcript.py` (phrases, rendu `[début-fin] S<n>: texte`, blocs recouvrants), `llm.py` (appel Claude, `extract_json`, coût), `selection.py` (prompt, `parse_candidates`, `dedupe`), `costs.py`, `db.py`.
- Données : `data/videos/<source_id>/{source.mkv,info.json,transcript.json,proxy.mp4}`, `data/clips/<id>/`.

## Conventions
- Code, commentaires, logs et messages en **français**. Logs via `clipper.log.log`.
- Prompts dans `prompts/*.md` avec variables `{{nom}}` (`llm.load_prompt` échoue si une variable reste non remplacée). Ne jamais mettre de prompt en dur dans le code.
- Idempotence : écrire les fichiers en `.tmp` puis `replace()` ; écritures DB multi-lignes dans `with db.transaction(conn)`.
- Jamais d'appel externe en double : transcription en cache, sélection écrite atomiquement avec le changement d'étape.
- Tout coût d'API passe par `costs.record` (affiché par `clipper status`).
- Pas de dépendance ajoutée sans raison écrite ici.
- Tests sans réseau ni clé (`tests/`, fixtures `conn` et `cfg` dans `conftest.py`).

## État
- Phase 1 (ingestion, transcription, sélection) : codée, en attente de démo sur vraie vidéo.
