import subprocess
import json
from unittest.mock import patch

import pytest

import morning_news as news
import news_io
from edition_process import run_isolated


class Response:
    is_redirect = False
    headers = {}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def raise_for_status(self): pass
    def iter_content(self, size): yield b"article"


def test_last_allowed_request_completes_and_next_is_blocked(monkeypatch):
    monkeypatch.setattr(news_io, "public_url", lambda url: url)
    monkeypatch.setattr(news_io.requests, "get", lambda *a, **kw: Response())
    reader = news_io.WebReader(max_requests=1)
    assert reader.get("https://example.test/1")[0] == b"article"
    assert reader.get("https://example.test/1")[0] == b"article"
    with pytest.raises(news_io.BudgetExhausted):
        reader.get("https://example.test/2")
    reader.deadline = 0
    with pytest.raises(news_io.BudgetExhausted, match="time"):
        reader.get("https://example.test/1")


def test_timeout_kills_and_reaps_child_before_return(monkeypatch):
    events = []
    class Process:
        def communicate(self, *a, **kw):
            raise subprocess.TimeoutExpired("worker", 1)
        def kill(self): events.append("kill")
        def wait(self): events.append("wait")
    monkeypatch.setattr("edition_process.subprocess.Popen", lambda *a, **kw: Process())
    with pytest.raises(subprocess.TimeoutExpired):
        run_isolated({"collection": {"max_seconds": 1}})
    assert events == ["kill", "wait"]


def test_successful_delivery_survives_optional_failures(monkeypatch, tmp_path):
    settings = news.load_settings()
    settings["edition"]["minimum_articles"] = 1
    article = {"title": "Ny historie", "source": "DR", "url": "https://example.test/a"}
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "test")
    monkeypatch.setattr(news, "fetch_candidates", lambda *a: [article])
    monkeypatch.setattr(news, "fetch_web_candidates", lambda *a: [])
    monkeypatch.setattr(news, "published_history", lambda *a: {})
    monkeypatch.setattr(news, "enrich_candidate_previews", lambda *a: ([article], 0, False))
    monkeypatch.setattr(news, "select_articles", lambda *a: [article])
    monkeypatch.setattr(news, "prepare_article", lambda a, r: dict(a, body="text"))
    monkeypatch.setattr(news, "build_epub", lambda *a, **kw: tmp_path / "edition.epub")
    monkeypatch.setattr(news, "upload_to_drive", lambda *a: {"id": "delivered"})
    def unavailable(*a, **kw): raise OSError("Unavailable")
    monkeypatch.setattr(news, "editorial_note", unavailable)
    monkeypatch.setattr(news, "prune_local_editions", unavailable)
    with patch.object(news, "record_edition", return_value=True) as archive:
        result = news._run_edition(settings)
    assert result["drive_file"] == {"id": "delivered"}
    assert result["github_saved"] is True
    archive.assert_called_once()


def test_wait_route_cannot_bypass_running_job(monkeypatch):
    import app
    monkeypatch.setenv("RUN_NOW_TOKEN", "test")
    # Keep the first job pending without starting external work.
    monkeypatch.setattr(app.threading.Thread, "start", lambda self: None)
    client = app.create_app(start_scheduler=False).test_client()
    headers = {"Authorization": "Bearer test"}
    assert client.post("/run-now?wait=true", headers=headers).status_code == 202
    assert client.post("/run-now?wait=true", headers=headers).status_code == 409


def test_wrong_title_for_valid_id_triggers_retry(monkeypatch):
    from types import SimpleNamespace
    responses = iter(["Forkert historie", "Kulturpolitik"])
    calls = []
    def create(**kwargs):
        calls.append(1)
        return SimpleNamespace(usage=None, output_text=json.dumps({"selected": [
            {"candidate_id": "c000", "source_title": next(responses), "why": "Forklaring"}]}))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(news, "OpenAI", lambda **kw: SimpleNamespace(responses=SimpleNamespace(create=create)))
    prompt = json.dumps({"fixed_profile": {}, "candidates": [
        {"candidate_id": "c000", "title": "Kulturpolitik"}]})
    result = news.ai_call_with_retry(prompt, news.load_settings(), pause=0)
    assert result["selected"][0]["source_title"] == "Kulturpolitik"
    assert len(calls) == 2
