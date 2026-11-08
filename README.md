# Clipper

Transforme des vidéos YouTube longues (podcasts, interviews, formations) en clips horizontaux de 2 à 5 minutes, notés, titrés et avec miniature, puis les publie en programmé sur une chaîne de clips.

> Pipeline complet : ingestion, transcription, sélection, bornes, titres, miniatures, revue, rendu, publication programmée, suivi des vues.

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

### 5. Connexion YouTube (OAuth) — une seule fois
Nécessaire uniquement pour `clipper publish` (et `clipper stats`).

1. Ouvre https://console.cloud.google.com et crée un projet (ex. « Clipper »).
2. **API et services → Bibliothèque** : active **YouTube Data API v3** et **YouTube Analytics API**.
3. **Google Auth Platform** (ex-« Écran de consentement OAuth ») → *Commencer* : nom de l'application, e-mail d'assistance, audience **Externe**, e-mail de contact.
4. **Audience → Utilisateurs test** : ajoute le compte Google qui gère la chaîne de clips.
   Puis, pour éviter que la connexion expire tous les 7 jours, clique **Publier l'application** (statut « En production »). Pour un usage personnel, aucune validation Google n'est nécessaire : à la connexion, Google affichera « Application non validée » → *Paramètres avancés* → *Accéder à Clipper*.
5. **Clients → Créer un client** → type **Application de bureau** → télécharge le JSON et enregistre-le à la racine du projet sous le nom `client_secret.json` (ou indique son chemin dans `YOUTUBE_CLIENT_SECRETS` du `.env`).
6. Lance :
   ```bash
   clipper publish --login
   ```
   Le navigateur s'ouvre : choisis le compte, **puis la chaîne de clips** si le compte en gère plusieurs. Clipper affiche « Connecté à la chaîne « … » » : vérifie que c'est la bonne. Le jeton est enregistré dans `data/youtube_token.json` (ne le partage pas). Pour changer de chaîne, relance `clipper publish --login`.
7. **Vérifie la chaîne** (numéro de téléphone) sur https://www.youtube.com/verify : sans cela, YouTube refuse les miniatures personnalisées (Clipper envoie quand même la vidéo et retentera la miniature au prochain `publish`).
8. **Important — audit de l'API.** YouTube verrouille en **privé** les vidéos envoyées par un projet Google Cloud non audité (règle en vigueur depuis juillet 2020), même avec une date de publication programmée. Remplis le formulaire d'audit *YouTube API Services* (https://support.google.com/youtube/contact/yt_api_form) en décrivant l'usage : envoi de clips de tes propres vidéos sur ta chaîne. Le même formulaire sert à demander plus de quota. En attendant la réponse, teste avec un seul clip (`clipper publish --max 1`).

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
clipper publish --dry-run                                     # montre les créneaux prévus, sans rien envoyer
clipper publish                                               # envoie les clips validés (privés, programmés)
clipper stats                                                 # relève vues et durée moyenne à J+2 et J+7
clipper stats --csv stats.csv                                 # … et exporte le tableau
```

Routine conseillée (par exemple chaque matin) :
```bash
clipper add https://www.youtube.com/@machaine && clipper run && clipper publish && clipper stats
```
puis `clipper review` quand tu as un moment pour valider les nouveaux clips.

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

### Publication
- La file = les clips validés, dans l'ordre où tu les as validés. Chaque clip reçoit le prochain créneau libre défini dans `config.yaml` (`publish.slots`, `publish.days`, `publish.timezone`, au moins `min_lead_hours` d'avance).
- Chaque vidéo est envoyée en **privé** avec une date de publication : c'est YouTube qui la rend publique au créneau. Rien n'est jamais publié directement.
- Titre, description (avec lien et timecode de la vidéo source), tags, catégorie, langue, miniature et playlist (`publish.playlist_id`) sont renseignés.
- **Quota** : 10 000 unités par jour, remises à zéro à minuit heure du Pacifique (9 h à Paris). Un clip coûte 1 600 (envoi) + 50 (miniature) + 50 (playlist) = **1 700 unités → 5 clips par jour** avec playlist, 6 sans. `clipper publish` et `clipper status` affichent le reste ; quand il ne suffit plus, la publication s'arrête proprement et la file reste intacte pour le lendemain.
- Un envoi interrompu (coupure, crash) est retrouvé sur la chaîne au passage suivant au lieu d'être renvoyé en double.
- `clipper publish --max 2` limite le nombre d'envois.

### Suivi
- `clipper stats` relève, pour chaque clip publié, les **vues** et la **durée moyenne de visionnage** sur ses 2 premiers jours puis ses 7 premiers jours (`stats.checkpoints_days`), et les enregistre avec le titre et la note donnée par Claude.
- Les données de YouTube Analytics arrivent avec 1 à 3 jours de retard : le relevé J+2 est souvent fait vers J+4, mais il mesure bien les 2 premiers jours. Lance `clipper stats` régulièrement, il ne fait que les relevés dus. Après 5 jours sans données, il enregistre le compteur de vues actuel sans durée moyenne.
- Le tableau affiche aussi la **rétention** (durée moyenne ÷ durée du clip) : pratique pour comparer la note de Claude à la performance réelle et ajuster `prompts/selection.md`.
- `--csv` exporte un fichier séparé par `;` qui s'ouvre directement dans Excel ou Numbers en français.
- Coût : 1 unité de quota YouTube par lot de 50 vidéos ; l'API Analytics a son propre quota, largement suffisant.

### Robustesse
- Toutes les commandes sont **idempotentes** : relancer `clipper run` après un crash reprend là où ça s'est arrêté, sans retélécharger ni retranscrire.
- Une vidéo en échec n'empêche pas les autres d'avancer. Après 3 échecs elle est mise de côté ; `clipper run --retry` la retente.
- `clipper run --video 3` ne traite que la vidéo 3. `-v` affiche les logs détaillés. Journal complet dans `data/clipper.log`.

## Coûts indicatifs (vidéo d'1 h)
- Transcription Deepgram nova-3 + diarisation : ~0,38 $
- Sélection Claude Sonnet 5.5 : ~0,05 à 0,15 $ (dépend de la longueur et du niveau de réflexion)

`clipper status` affiche le coût estimé par vidéo et le cumul (tarifs dans `config.yaml`).

## Dépannage
- **« Sign in to confirm you're not a bot » / HTTP 403** : YouTube bloque parfois les téléchargements. Renseigne `ingest.cookies_from_browser: chrome` (ou `firefox`) dans `config.yaml`, ou exporte un `cookies.txt` et indique-le dans `ingest.cookies_file`. Mets aussi yt-dlp à jour : `pip install -U "yt-dlp[default]"`.
- **Fichier OAuth introuvable** : place `client_secret.json` à la racine (étape 5 de la connexion YouTube).
- **« Jeton YouTube expiré »** : l'application Google est restée en mode « Test » (jeton valable 7 jours). Publie-la (étape 4) puis `clipper publish --login`.
- **Vidéos bloquées en privé malgré la date** : le projet n'a pas encore passé l'audit API (étape 8).
- **Variable … absente** : la clé n'est pas dans `.env`, ou tu ne lances pas la commande depuis le dossier du projet (sinon définis `CLIPPER_HOME=/chemin/vers/clipper`).

## Tests
```bash
python -m pytest -q
```
Aucun test n'appelle le réseau ni n'a besoin de clé.
