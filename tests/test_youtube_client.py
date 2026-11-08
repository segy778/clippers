"""Client YouTube réel (googleapiclient) contre des réponses HTTP simulées : aucun appel réseau."""
import json

import pytest
from googleapiclient.discovery import build
from googleapiclient.http import HttpMockSequence

from clipper.youtube import QuotaExceeded, ThumbnailForbidden, YouTube


def client(responses):
    yt = YouTube.__new__(YouTube)  # sans OAuth
    http = HttpMockSequence(responses)
    yt.api = build("youtube", "v3", http=http, static_discovery=True)
    return yt, http


def err(status, reason):
    body = {"error": {"code": status, "message": reason, "errors": [{"reason": reason, "message": reason}]}}
    return ({"status": str(status)}, json.dumps(body))


def test_channel():
    yt, _ = client([({"status": "200"}, json.dumps({"items": [{"id": "UC1", "snippet": {"title": "Mes clips"},
                    "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}))])
    assert yt.channel() == {"id": "UC1", "title": "Mes clips", "uploads": "UU1"}


def test_resumable_upload_sends_metadata(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"0" * 1000)
    yt, http = client([
        ({"status": "200", "location": "https://upload.example/session"}, ""),
        ({"status": "200"}, json.dumps({"id": "VID123"})),
    ])
    body = {"snippet": {"title": "T"}, "status": {"privacyStatus": "private", "publishAt": "2026-11-09T17:00:00Z"}}
    assert yt.upload(f, body) == "VID123"


def test_quota_error_translated():
    yt, _ = client([err(403, "quotaExceeded")])
    with pytest.raises(QuotaExceeded):
        yt.add_to_playlist("PL1", "VID")


def test_thumbnail_forbidden(tmp_path):
    f = tmp_path / "t.jpg"
    f.write_bytes(b"jpg")
    yt, _ = client([err(403, "forbidden")])
    with pytest.raises(ThumbnailForbidden):
        yt.set_thumbnail("VID", f)


def test_view_counts_and_analytics_report():
    yt, _ = client([({"status": "200"}, json.dumps({"items": [
        {"id": "V1", "statistics": {"viewCount": "42"}, "status": {"privacyStatus": "public"}}]}))])
    assert yt.view_counts(["V1"]) == {"V1": {"views": 42, "privacy": "public"}}
    yt.analytics = build("youtubeAnalytics", "v2", http=HttpMockSequence([
        ({"status": "200"}, json.dumps({"rows": [[120, 88.5]]})),
        ({"status": "200"}, json.dumps({"rows": []})),
    ]), static_discovery=True)
    assert yt.video_report("V1", "2026-11-02", "2026-11-04") == {"views": 120, "avg_view_duration": 88.5}
    assert yt.video_report("V1", "2026-11-02", "2026-11-04") is None
