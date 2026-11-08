# Clipper

## But
Transformer des vidéos YouTube longues (podcasts, interviews, formations — droits détenus par l'utilisateur) en clips horizontaux de 2 à 5 min, notés, titrés, avec miniature ; revue locale en un clic ; publication programmée en privé sur une chaîne de clips ; suivi des vues.

## Stack
- Python ≥ 3.11, CLI `argparse` (`clipper/cli.py`), SQLite stdlib (`data/clipper.db`).
- yt-dlp (API Python), Deepgram REST via httpx (nova-3, diarisation), SDK `anthropic` (Claude Sonnet 5.5 par défaut, sortie JSON contrainte via `output_config.format`, repli serveur `fallbacks="default"`), ffmpeg en sous-processus.
- Revue : FastAPI + uvicorn, pages HTML/JS vanilla (`clipper/web/static/`), sans framework front ni Jinja.
- Miniatures : Pillow (texte) + `opencv-python-headless<5` (netteté = variance du Laplacien, visages = cascade Haar ; OpenCV 5 a retiré les cascades).
- Publication : google-api-python-client + google-auth-oauthlib (OAuth « application de bureau », jeton dans `data/youtube_token.json`, scopes upload + youtube + yt-analytics.readonly demandés d'emblée pour éviter un 2e consentement en phase 4).
- Clés dans `.env` (lu par `config.load_env`, pas de python-dotenv), réglages dans `config.yaml` (fusionnés sur `config.DEFAULTS`).

## Commandes
```bash
pip install -e ".[dev]"        # installation
python -m pytest -q            # tests
clipper add URL|fichier        # vidéo, playlist, chaîne ou fichier local
clipper run [--video ID] [--retry]
clipper status [ID]            # tableau + coûts ; avec ID : détail des clips
clipper review [--port N] [--no-browser]   # page de revue locale
clipper publish [--dry-run] [--max N] [--login] [--no-browser]
clipper stats                  # phase 4
```

## Architecture
- `pipeline.py` : machine à états par vidéo (`videos.stage` = dernière étape réussie : new → downloaded → transcribed → selected → bounded → titled → ready). Fin de `run` : rendu des clips validés en attente (`render.render_pending`). Chaque étape vérifie son artefact avant d'agir. Une exception marque `error`/`attempts` et passe à la vidéo suivante (3 échecs max sauf `--retry`).
- `ingest.py` (yt-dlp, proxy 720p), `transcribe.py` (backend + cache `transcript.json`), `transcript.py` (phrases, rendu `[début-fin] S<n>: texte`, blocs recouvrants), `llm.py` (appel Claude, `extract_json`, coût), `selection.py` (prompt, `parse_candidates`, `dedupe`), `bounds.py` (`snap` : recalage phrase + marge bornée par les voisins + contrôle durée), `titles.py` (1 appel/vidéo, nettoyage ≤ 60 car., pied de description calculé à la volée depuis `start`), `thumbs.py`, `render.py` (loudnorm 2 passes linéaire, `is_fresh` = rendu aligné sur les bornes actuelles), `web/app.py` (API + file de rendu à un seul thread), `quota.py` (jour = date à Los Angeles, décompte AVANT chaque appel, `exhaust` si YouTube dit quotaExceeded), `publish.py` (file = validés par `approved_at`, `next_slots`, envoi privé + `publishAt`, miniature/playlist suivies par `thumb_set`/`playlist_added`, reprise d'un envoi interrompu via `upload_started_at` + recherche du titre dans les 50 derniers envois), `youtube.py` (seul module qui parle à Google ; remplacé par `FakeYT` dans les tests), `costs.py`, `db.py` (`MIGRATIONS` pour les colonnes ajoutées).
- Statuts de clip : candidate → approved → rendered → uploaded ; rejected ; auto_rejected (durée hors limites = repêchable en revue ; « Chevauche… » = masqué). Modifier les bornes d'un clip rendu le repasse en approved (re-rendu).
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
- Phase 1 (ingestion, transcription, sélection) : validée par l'utilisateur.
- Phase 2 (bornes, titres, miniatures, rendu, revue) : validée par l'utilisateur.
- Phase 3 (OAuth, publication, quota) : codée et testée (faux client YouTube + googleapiclient sur réponses HTTP simulées) ; démo réelle à faire chez l'utilisateur.
- Tests : `test_pipeline.py` simule Deepgram/Claude et les étapes ffmpeg lourdes ; `test_render_thumbs.py` utilise le vrai ffmpeg.
