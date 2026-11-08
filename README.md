# Clipper

Transforme des vidéos YouTube longues (podcasts, interviews, formations) en clips horizontaux de 2 à 5 minutes, notés, titrés et avec miniature, puis les publie en programmé sur une chaîne de clips.

> État : **phases 1 et 2** (ingestion, transcription, sélection, bornes, titres, miniatures, revue, rendu). Publication en phase 3, suivi en phase 4.

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
Tout est dans `config.yaml` (commenté) : langue de transcription, modèle Claude, nombre de clips, durées, rendu, miniatures (police, taille, couleurs, contour, position, bandeau), cadence de publication, tarifs utilisés pour estimer les coûts.

Conseil miniatures : une police grasse et condensée (Anton, Bebas Neue, Montserrat ExtraBold — gratuites sur Google Fonts) rend mieux que la police par défaut ; indique son chemin dans `thumbnail.font_path`.

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
clipper review                                                # ouvre la page de revue dans le navigateur
```

### Ce que fait `clipper run`
1. Télécharge la vidéo (≤ 1080p) et ses métadonnées.
2. Transcrit (mots horodatés + locuteurs), une seule fois.
3. Demande à Claude 5 à 15 candidats (début, fin, sujet, note, raison).
4. Recale chaque clip sur des frontières de phrase avec 0,3 s de marge ; écarte (avec motif) les durées hors 2–5 min et les chevauchements.
5. Rédige 3 titres, une description, 10 tags et le texte de miniature de chaque clip.
6. Crée la miniature (image la plus nette avec un visage parmi 10, texte posé) et une copie 720p légère pour la revue.
7. Rend les clips que tu as validés (1080p, fondu audio 0,3 s, −14 LUFS).

### Page de revue
Une page par vidéo. Le lecteur joue le clip actif dans ses bornes ; tout est sauvegardé immédiatement en base. Valider lance le rendu final en arrière-plan.

| Touche | Action |
|---|---|
| `J` / `↓`, `K` / `↑` | clip suivant / précédent |
| `Espace` | lire / pause dans les bornes |
| `D` / `F` | écouter le début / les 6 dernières secondes |
| `V` / `X` / `U` | valider / rejeter / annuler la décision |
| `A` / `Z` | début −1 s / +1 s |
| `Q` / `S` | fin −1 s / +1 s |
| `1` `2` `3` | choisir le titre |
| `E` / `T` | modifier le titre / le texte de miniature |
| `?` | aide |

Clique sur une des 10 vignettes pour changer l'image de la miniature. Les clips écartés automatiquement (trop courts ou trop longs) restent affichés : ajuste leurs bornes puis valide pour les repêcher.

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
