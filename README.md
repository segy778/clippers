# Clipper

Transforme des vidéos YouTube longues (podcasts, interviews, formations) en clips horizontaux de 2 à 5 minutes, notés, titrés et avec miniature, puis les publie en programmé sur une chaîne de clips.

> État : **phase 1** (ingestion, transcription, sélection des candidats). Revue, découpe et miniatures arrivent en phase 2, publication en phase 3, suivi en phase 4.

## Installation pas à pas

### 1. Prérequis système
- **Python 3.11+**
- **ffmpeg** (avec ffprobe) : `brew install ffmpeg` (macOS) · `sudo apt install ffmpeg` (Debian/Ubuntu) · `winget install ffmpeg` (Windows)
- **Deno** (recommandé) : yt-dlp en a besoin pour lire certains formats YouTube. `brew install deno` ou `curl -fsSL https://deno.land/install.sh | sh`. Node.js fonctionne aussi.

### 2. Installer Clipper
```bash
git clone <ce dépôt> clipper && cd clipper
python3 -m venv .venv && source .venv/bin/activate     # Windows : .venv\Scripts\activate
pip install -e ".[dev]"
```

### 3. Clés d'API
```bash
cp .env.example .env
```
Renseigne dans `.env` :
- `ANTHROPIC_API_KEY` : https://console.anthropic.com/settings/keys
- `DEEPGRAM_API_KEY` : https://console.deepgram.com → *API Keys* (200 $ de crédit offert à l'inscription)

### 4. Réglages
Tout est dans `config.yaml` (commenté) : langue de transcription, modèle Claude, nombre de clips, durées, rendu, miniatures, cadence de publication, tarifs utilisés pour estimer les coûts.

Les prompts envoyés à Claude sont dans `prompts/*.md` : modifie-les librement, les `{{variables}}` sont remplacées automatiquement.

## Utilisation
```bash
clipper add https://www.youtube.com/watch?v=XXXXXXXXXXX     # une vidéo
clipper add https://www.youtube.com/playlist?list=...        # une playlist
clipper add https://www.youtube.com/@machaine                # une chaîne (onglet Vidéos)
clipper add ~/Videos/episode-12.mp4                          # un fichier local
clipper run                                                   # télécharge, transcrit, sélectionne
clipper status                                                # état, nombre de clips, coût par vidéo et cumul
clipper status 1                                              # détail des candidats de la vidéo 1
```

- Toutes les commandes sont **idempotentes** : relancer `clipper run` après un crash reprend là où ça s'est arrêté, sans retélécharger ni retranscrire.
- Une vidéo en échec n'empêche pas les autres d'avancer. Après 3 échecs elle est mise de côté ; `clipper run --retry` la retente.
- `clipper run --video 3` ne traite que la vidéo 3. `-v` affiche les logs détaillés. Journal complet dans `data/clipper.log`.

## Coûts indicatifs (vidéo d'1 h)
- Transcription Deepgram nova-3 + diarisation : ~0,38 $
- Sélection Claude Sonnet 5.5 : ~0,05 à 0,15 $ (dépend de la longueur et du niveau de réflexion)

`clipper status` affiche le coût estimé par vidéo et le cumul (tarifs dans `config.yaml`).

## Dépannage
- **« Sign in to confirm you're not a bot » / HTTP 403** : YouTube bloque parfois les téléchargements. Renseigne `ingest.cookies_from_browser: chrome` (ou `firefox`) dans `config.yaml`, ou exporte un `cookies.txt` et indique-le dans `ingest.cookies_file`. Mets aussi yt-dlp à jour : `pip install -U "yt-dlp[default]"`.
- **Variable … absente** : la clé n'est pas dans `.env`, ou tu ne lances pas la commande depuis le dossier du projet (sinon définis `CLIPPER_HOME=/chemin/vers/clipper`).

## Tests
```bash
python -m pytest -q
```
Aucun test n'appelle le réseau ni n'a besoin de clé.
