"""Accès à l'API YouTube : OAuth et les quelques appels utilisés.

Tout passe par la classe `YouTube`, que les tests remplacent par un faux client.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from .log import log

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",        # envoi des vidéos
    "https://www.googleapis.com/auth/youtube",               # miniatures, playlists, lecture des vidéos
    "https://www.googleapis.com/auth/yt-analytics.readonly",  # durée moyenne de visionnage (phase 4)
]
QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "uploadLimitExceeded"}
RETRY_STATUS = {500, 502, 503, 504}


class QuotaExceeded(RuntimeError):
    """Quota de l'API (ou limite d'envoi de la chaîne) atteint : on arrête pour aujourd'hui."""


class ThumbnailForbidden(RuntimeError):
    """La chaîne n'a pas le droit de mettre des miniatures personnalisées (compte non vérifié)."""


def _reason(err) -> str:
    try:
        return err.error_details[0].get("reason", "") if err.error_details else ""
    except (AttributeError, IndexError, TypeError):
        return ""


def _translate(err):
    """HttpError Google → exception explicite."""
    reason = _reason(err)
    if reason in QUOTA_REASONS or "quota" in str(err).lower():
        return QuotaExceeded(f"YouTube refuse : {reason or err}")
    return err


def token_path(cfg) -> Path:
    return cfg.data_dir / "youtube_token.json"


def get_credentials(cfg, force_login: bool = False, open_browser: bool = True):
    """Charge le jeton OAuth, le rafraîchit, ou lance la connexion dans le navigateur."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    path = token_path(cfg)
    creds = None
    if path.exists() and not force_login:
        creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:  # jeton révoqué ou expiré (7 jours en mode « test » Google)
            log.warning("Jeton YouTube expiré (%s) : nouvelle connexion nécessaire.", exc)
            creds = None
    if not creds or not creds.valid:
        secrets = Path(os.environ.get("YOUTUBE_CLIENT_SECRETS", "client_secret.json"))
        if not secrets.is_absolute():
            secrets = cfg.root / secrets
        if not secrets.exists():
            raise RuntimeError(f"Fichier OAuth introuvable : {secrets}. Voir README, section « Connexion YouTube ».")
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
        log.info("Connexion à YouTube : choisis le compte ET la chaîne de clips dans la fenêtre du navigateur.")
        creds = flow.run_local_server(port=0, open_browser=open_browser,
                                      authorization_prompt_message="Ouvre cette adresse pour autoriser Clipper : {url}",
                                      success_message="Clipper est autorisé. Tu peux fermer cet onglet.")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(creds.to_json(), encoding="utf-8")
    tmp.replace(path)
    path.chmod(0o600)
    return creds


class YouTube:
    def __init__(self, creds):
        from googleapiclient.discovery import build

        self.api = build("youtube", "v3", credentials=creds, cache_discovery=False)
        self.analytics = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
        self.creds = creds

    def _exec(self, request):
        from googleapiclient.errors import HttpError

        try:
            return request.execute(num_retries=3)
        except HttpError as err:
            raise _translate(err) from err

    def channel(self) -> dict:
        """Chaîne connectée : {id, title, uploads} (1 unité)."""
        res = self._exec(self.api.channels().list(part="snippet,contentDetails", mine=True))
        if not res.get("items"):
            raise RuntimeError("Ce compte Google n'a pas de chaîne YouTube.")
        item = res["items"][0]
        return {"id": item["id"], "title": item["snippet"]["title"],
                "uploads": item["contentDetails"]["relatedPlaylists"]["uploads"]}

    def recent_uploads(self, uploads_playlist: str) -> list[dict]:
        """50 derniers envois de la chaîne : [{id, title}] (1 unité)."""
        res = self._exec(self.api.playlistItems().list(part="snippet", playlistId=uploads_playlist, maxResults=50))
        return [{"id": it["snippet"]["resourceId"]["videoId"], "title": it["snippet"]["title"]}
                for it in res.get("items", [])]

    def upload(self, path: Path, body: dict) -> str:
        """Envoi reprenable par morceaux de 8 Mo. Renvoie l'identifiant de la vidéo (1 600 unités)."""
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(str(path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
        request = self.api.videos().insert(part="snippet,status", body=body, media_body=media,
                                           notifySubscribers=True)
        response, attempt, last_pct = None, 0, -10
        while response is None:
            try:
                status, response = request.next_chunk()
                attempt = 0
                if status and status.progress() * 100 >= last_pct + 10:
                    last_pct = int(status.progress() * 100)
                    log.info("  envoi : %d %%", last_pct)
            except HttpError as err:
                if err.resp.status in RETRY_STATUS and attempt < 5:
                    attempt += 1
                    time.sleep(2 ** attempt)
                    continue
                raise _translate(err) from err
            except (ConnectionError, TimeoutError, OSError):
                if attempt >= 5:
                    raise
                attempt += 1
                time.sleep(2 ** attempt)
        return response["id"]

    def set_thumbnail(self, video_id: str, path: Path) -> None:
        """50 unités. Exige une chaîne vérifiée (numéro de téléphone)."""
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        try:
            self.api.thumbnails().set(videoId=video_id,
                                      media_body=MediaFileUpload(str(path), mimetype="image/jpeg")).execute(num_retries=3)
        except HttpError as err:
            if err.resp.status == 403 and _reason(err) not in QUOTA_REASONS:
                raise ThumbnailForbidden(str(err)) from err
            raise _translate(err) from err

    def add_to_playlist(self, playlist_id: str, video_id: str) -> None:
        """50 unités."""
        self._exec(self.api.playlistItems().insert(part="snippet", body={"snippet": {
            "playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}))

    def view_counts(self, video_ids: list[str]) -> dict[str, dict]:
        """Vues actuelles et statut de visibilité, par lots de 50 (1 unité par lot)."""
        out = {}
        for i in range(0, len(video_ids), 50):
            res = self._exec(self.api.videos().list(part="statistics,status", id=",".join(video_ids[i:i + 50])))
            for it in res.get("items", []):
                out[it["id"]] = {"views": int(it["statistics"].get("viewCount", 0)),
                                 "privacy": it["status"]["privacyStatus"]}
        return out

    def video_report(self, video_id: str, start: str, end: str) -> dict | None:
        """Vues et durée moyenne de visionnage (s) entre deux dates (YYYY-MM-DD, heure du Pacifique).

        API YouTube Analytics (quota séparé de l'API Data). Renvoie None si les données
        ne sont pas encore disponibles (délai habituel de 1 à 3 jours).
        """
        res = self._exec(self.analytics.reports().query(
            ids="channel==MINE", startDate=start, endDate=end, metrics="views,averageViewDuration",
            filters=f"video=={video_id}"))
        rows = res.get("rows") or []
        if not rows or rows[0][0] in (None, 0) and rows[0][1] in (None, 0):
            return None
        return {"views": int(rows[0][0]), "avg_view_duration": float(rows[0][1])}
