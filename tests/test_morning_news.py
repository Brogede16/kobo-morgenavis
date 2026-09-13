import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from feedback_store import editorial_note
from morning_news import build_epub, editorial_prompt_profile, enforce_source_diversity, enrich_candidate_previews, load_settings, merge_candidate_pools, prune_old_drive_editions, select_articles
from newspaper import overview_group


def settings():
    return {"edition": {"title": "Test Avis", "language": "da", "max_articles": 2, "max_candidates": 180, "ai_shortlist_size": 80, "max_summary_characters": 260,
                         "timezone": "Europe/Copenhagen", "schedule": "30 5 * * *", "topics": ["teknologi"]},
            "web_search": {"enabled_for_schedule": True, "max_queries": 3, "queries": ["test"]},
            "images": {"enabled": False},
            "sources": [{"name": "Test", "url": "https://example.test/feed"}]}


def test_load_settings(tmp_path):
    source = tmp_path / "sources.yaml"
    source.write_text("edition:\n  title: A\n  language: da\n  max_articles: 1\n  max_candidates: 180\n  ai_shortlist_size: 80\n  max_summary_characters: 260\n  timezone: Europe/Copenhagen\n  schedule: '0 5 * * *'\n  topics: [nyheder]\nsources:\n  - name: A\n    url: https://example.test\n")
    assert load_settings(source)["edition"]["title"] == "A"


def test_select_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    candidates = [{"title": str(i), "source": "x", "url": f"https://x/{i}", "summary": "s"} for i in range(3)]
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        select_articles(candidates, settings())


def test_build_epub(tmp_path):
    article = {"title": "Historie", "source": "Kilde", "url": "https://example.test", "summary": "Kort",
               "body": "<div><p>Tekst</p></div>", "why": "Relevant", "reading_minutes": 1}
    path = build_epub([article], settings(), tmp_path)
    assert path.exists() and path.suffix == ".epub"


def test_overview_groups_make_the_newspaper_scannable():
    assert overview_group({"section": "Dansk politik"}) == "Danmark og kultur"
    assert overview_group({"section": "AI og teknologi"}) == "Teknologi og verden"
    assert overview_group({"section": "Rumforskning"}) == "Fordybelse"


def test_editorial_note_is_explicit_about_feedback(monkeypatch):
    monkeypatch.setattr("feedback_store.recent_feedback", lambda limit: [
        {"created_at": "2026-09-08T08:00:00+00:00", "direction": "more", "reason": "great_depth"},
        {"created_at": "2026-09-08T08:00:00+00:00", "direction": "less", "reason": "too_thin"},
    ])
    assert "1 'mere' og 1 'mindre'" in editorial_note()
    assert "god dybde" in editorial_note()


def test_model_json_parser_repairs_only_bare_object_keys(monkeypatch):
    class Client:
        class Responses:
            def create(self, **kwargs):
                return type("Response", (), {"output_text": '[]\n{selected:[{"i": 1, "why": "ok",},],}', "usage": None})()
        responses = Responses()
    monkeypatch.setattr("morning_news.OpenAI", lambda **kwargs: Client())
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    from morning_news import ai_call
    assert ai_call("test", settings())["selected"][0]["i"] == 1


def test_editorial_prompt_profile_caps_examples(monkeypatch, tmp_path):
    profile = tmp_path / "editorial.yaml"
    profile.write_text("editorial: {voice: Calm}\nexamples:\n  read:\n" + "\n".join(f"    - url: https://x/{i}\n      reason: useful" for i in range(15)))
    monkeypatch.setattr("morning_news.load_editorial_profile", lambda: __import__("yaml").safe_load(profile.read_text()))
    assert len(editorial_prompt_profile()["examples"]["read"]) == 8


def test_editorial_prompt_profile_uses_compact_priority_tiers(monkeypatch):
    profile = {
        "editorial": {
            "voice": "V",
            "priorities": ["legacy"] * 50,
            "priority_tiers": {"daily_core": [str(i) for i in range(10)]},
            "daily_structure": [str(i) for i in range(10)],
            "exclusions": [str(i) for i in range(20)],
        },
        "examples": {},
    }
    monkeypatch.setattr("morning_news.load_editorial_profile", lambda: profile)
    compact = editorial_prompt_profile()["editorial"]
    assert "priorities" not in compact
    assert len(compact["priority_tiers"]["daily_core"]) == 10
    assert len(compact["daily_structure"]) == 10


def test_source_diversity_caps_a_single_outlet():
    items = [{"title": str(i), "source": "One", "url": f"https://one/{i}", "summary": ""} for i in range(3)]
    items += [{"title": "other", "source": "Two", "url": "https://two/1", "summary": ""}]
    configured = settings()
    configured["edition"]["max_articles"] = 3
    configured["edition"]["max_articles_per_source"] = 2
    assert [item["source"] for item in enforce_source_diversity(items, items, configured)] == ["One", "One", "Two"]


def test_web_candidates_are_reserved_before_global_cap():
    configured = settings()
    configured["edition"].update({"max_candidates": 4, "web_candidate_reserve": 2})
    feeds = [{"title": str(i), "source": "RSS", "url": f"https://rss/{i}", "summary": ""} for i in range(5)]
    web = [{"title": str(i), "source": "Web", "url": f"https://web/{i}", "summary": ""} for i in range(2)]
    assert [item["source"] for item in merge_candidate_pools(feeds, web, configured)] == ["RSS", "RSS", "Web", "Web"]


def test_candidate_previews_are_enriched_without_an_ai_call():
    class Reader:
        def get(self, url, limit=None):
            page = b'<html><head><meta name="description" content="Useful context"></head><body><article><p>First paragraph with substance.</p><p>Second paragraph.</p></article></body></html>'
            return page, "text/html", url

    configured = settings()
    configured["collection"] = {"max_candidate_previews": 1}
    items = [{"title": "A", "source": "One", "url": "https://one.test/a", "summary": ""}]
    result, count = enrich_candidate_previews(items, configured, Reader())
    assert count == 1
    assert "Useful context" in result[0]["summary"]


def test_drive_retention_deletes_only_editions_beyond_ten():
    class Files:
        def __init__(self):
            self.trashed = []

        def list(self, **kwargs):
            records = [{"id": str(i), "name": f"mads-morgen-2026-08-{i + 1:02d}.epub",
                        "mimeType": "application/epub+zip"} for i in range(12)]
            records.append({"id": "other", "name": "private.epub", "mimeType": "application/epub+zip"})
            return type("Request", (), {"execute": lambda _: {"files": records}})()

        def update(self, fileId, body):
            if body.get("trashed"):
                self.trashed.append(fileId)
            return type("Request", (), {"execute": lambda _: {}})()

    files = Files()
    prune_old_drive_editions(type("Drive", (), {"files": lambda _: files})(), "folder")
    assert files.trashed == ["1", "0"]


# --- Regression cover for the parts that fail quietly -------------------------


def test_overview_group_matches_whole_words_only():
    # "ukraine", "detailhandel" and "thailand" all contain "ai".
    assert overview_group({"section": "Nyheder", "title": "Ukraine efter forhandlingerne"}) == "Fordybelse"
    assert overview_group({"section": "Erhverv", "title": "Detailhandlen taber terræn"}) == "Fordybelse"
    assert overview_group({"section": "Teknologi", "title": "Ny AI-model fra Apple"}) == "Teknologi og verden"
    assert overview_group({"section": "Politik", "title": "Regeringen indgår forlig"}) == "Danmark og kultur"


def test_overview_group_prefers_the_editors_own_choice():
    article = {"section": "Teknologi", "title": "Ny chip", "group": "Fordybelse"}
    assert overview_group(article) == "Fordybelse"
    assert overview_group(dict(article, group="Noget opdigtet")) == "Teknologi og verden"


def test_every_editorial_policy_reaches_the_editor():
    from editorial import compact_profile
    profile = {"editorial": {"voice": "V", "place_and_systems_policy": "P", "en_helt_ny_regel": "N",
                             "priorities": ["x"]}, "examples": {}}
    brief = compact_profile(profile)["editorial"]
    assert brief["place_and_systems_policy"] == "P"
    assert brief["en_helt_ny_regel"] == "N"
    assert "priorities" not in brief


def test_recent_stories_are_not_reprinted():
    from editorial import drop_already_published
    history = {"urls": ["https://a.test/1"], "story_ids": ["valgkamp"],
               "titles": ["Regeringen udskyder den store klimaplan igen"]}
    candidates = [
        {"title": "Noget helt andet", "url": "https://a.test/1"},                     # same url
        {"title": "Ny sag", "url": "https://b.test/2", "story_id": "valgkamp"},       # same story
        {"title": "Regeringen udskyder den store klimaplan igen", "url": "https://c.test/3"},  # same headline
        {"title": "Museum åbner ny fløj i Aarhus", "url": "https://d.test/4"},        # keep
    ]
    assert [c["url"] for c in drop_already_published(candidates, history)] == ["https://d.test/4"]


def test_canonical_url_rejects_anything_but_plain_http():
    from news_io import canonical_url
    assert canonical_url("javascript:alert(1)") == ""
    assert canonical_url("https://user:pw@example.test/a") == ""
    assert canonical_url("https://example.test:8080/a") == ""
    assert canonical_url("HTTPS://Example.test/a?utm_source=x&b=2") == "https://example.test/a?b=2"


def test_public_url_blocks_private_destinations():
    from news_io import public_url
    for url in ("http://127.0.0.1/admin", "http://169.254.169.254/latest/meta-data",
                "http://10.0.0.5/", "file:///etc/passwd"):
        with pytest.raises(ValueError):
            public_url(url)


def test_sanitise_body_removes_scripts_and_keeps_readable_text():
    from newspaper import sanitise_body
    body = sanitise_body(
        '<div><p onclick="x()">Tekst <a href="/side" class="c">link</a></p>'
        '<script>alert(1)</script><iframe src="x"></iframe><img src="y.png"/></div>',
        "https://example.test/artikel")
    assert "script" not in body and "iframe" not in body and "<img" not in body
    assert "onclick" not in body and "class" not in body
    assert 'href="https://example.test/side"' in body
    assert "Tekst" in body


def test_paywalled_articles_never_reach_the_epub():
    from newspaper import prepare_article

    class Reader:
        def get(self, url, limit=None):
            page = (b'<html><head><script type="application/ld+json">'
                    b'{"isAccessibleForFree": false}</script></head><body><article>'
                    + b"<p>" + b"ord " * 800 + b"</p></article></body></html>")
            return page, "text/html", url

    assert prepare_article({"url": "https://paywall.test/a", "source": "P"}, Reader()) is None


def test_schedule_must_have_five_cron_fields(tmp_path):
    source = tmp_path / "sources.yaml"
    base = ("edition:\n  title: A\n  language: da\n  max_articles: 1\n  max_candidates: 180\n"
            "  ai_shortlist_size: 80\n  max_summary_characters: 260\n  timezone: Europe/Copenhagen\n"
            "  schedule: '{}'\n  topics: [nyheder]\nsources:\n  - name: A\n    url: https://example.test\n")
    source.write_text(base.format("30 5 * *"))
    with pytest.raises(ValueError, match="five cron fields"):
        load_settings(source)
    source.write_text(base.format("30 5 * * *"))
    assert load_settings(source)["edition"]["schedule"] == "30 5 * * *"


def test_old_feedback_stops_steering_the_selection(monkeypatch):
    import feedback_store
    old = "2020-01-01T08:00:00+00:00"
    recent = datetime.now(timezone.utc).isoformat()
    payload = "\n".join([
        json.dumps({"created_at": old, "direction": "more", "title": "gammel"}),
        json.dumps({"created_at": recent, "direction": "less", "title": "ny"}),
    ])
    monkeypatch.setattr(feedback_store, "_read", lambda path: (payload, "sha"))
    titles = [event["title"] for event in feedback_store.recent_feedback()]
    assert titles == ["ny"]


def test_cover_is_generated_without_system_fonts():
    from newspaper import build_cover_image
    from datetime import date
    raw = build_cover_image(
        [{"title": "Regeringen præsenterer en ny klimaplan for 2030", "reading_minutes": 4,
          "group": "Danmark og kultur"}],
        settings(), date(2026, 9, 12))
    with Image.open(io.BytesIO(raw)) as image:
        assert image.size == (1200, 1600)


def test_notice_epub_is_written_when_an_edition_fails(tmp_path):
    from newspaper import build_notice_epub
    path = build_notice_epub(settings(), RuntimeError("OPENAI_API_KEY mangler"), tmp_path)
    assert path.exists() and path.name.startswith("mads-morgen-")


def test_full_edition_runs_without_network(monkeypatch, tmp_path):
    """End-to-end cover for the wiring: history filter, grouping, report, EPUB."""
    import morning_news

    configured = load_settings()
    configured["edition"].update({"max_articles": 3, "minimum_articles": 2})
    configured["images"] = {"enabled": False}

    candidates = [
        {"title": "Regeringen indgår bredt forlig om kulturstøtte", "source": "DR", "url": "https://dr.test/1", "summary": "s", "pool": "rss"},
        {"title": "Ny AI-model kan køre lokalt på en Mac", "source": "Ars", "url": "https://ars.test/2", "summary": "s", "pool": "rss"},
        {"title": "Under København ligger et glemt tunnelnet", "source": "Atlas", "url": "https://atlas.test/3", "summary": "s", "pool": "rss"},
        {"title": "Gammel sag fra i går", "source": "DR", "url": "https://dr.test/old", "summary": "s", "pool": "rss"},
    ]
    monkeypatch.setattr(morning_news, "fetch_candidates", lambda *a, **k: list(candidates))
    monkeypatch.setattr(morning_news, "fetch_web_candidates", lambda *a, **k: [])
    monkeypatch.setattr(morning_news, "published_history", lambda editions=3: {
        "urls": ["https://dr.test/old"], "story_ids": [], "titles": []})
    monkeypatch.setattr(morning_news, "recent_feedback", lambda: [])
    monkeypatch.setattr(morning_news, "editorial_note", lambda: "note")
    monkeypatch.setattr(morning_news, "record_edition", lambda *a, **k: True)
    monkeypatch.setattr(morning_news, "prepare_article",
                        lambda article, reader: dict(article, body="<div><p>Tekst</p></div>", reading_minutes=3))
    monkeypatch.setattr(morning_news, "build_epub",
                        lambda prepared, settings, reader=None: build_epub(prepared, settings, tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.delenv("GOOGLE_DRIVE_FOLDER_ID", raising=False)

    sent = {}

    def fake_ai(prompt, settings, search=False, **kwargs):
        sent["prompt"] = prompt
        return {"selected": [
            {"i": 0, "section": "Politik", "group": "Danmark og kultur", "why": "a", "format": "short", "story_id": "forlig"},
            {"i": 1, "section": "Teknologi", "group": "Teknologi og verden", "why": "b", "format": "short", "story_id": "ai"},
            {"i": 2, "section": "Fordybelse", "group": "Fordybelse", "why": "c", "format": "longread", "story_id": "tunnel"},
        ], "backups": [], "gaps": []}

    monkeypatch.setattr(morning_news, "ai_call_with_retry", fake_ai)
    result = morning_news.run_edition(configured)

    assert result["articles"] == 3
    assert result["report"]["collection"]["repeats_dropped"] == 1
    assert result["report"]["extraction"] == {"approved": 3, "prepared": 3,
                                              "dropped_unreadable": 0, "dropped_diversity": 0}
    assert [a["group"] for a in result["article_list"]] == ["Danmark og kultur", "Teknologi og verden", "Fordybelse"]
    assert "https://dr.test/old" not in sent["prompt"]
    assert Path(result["path"]).exists()


def test_a_failing_edition_still_reaches_the_kobo(monkeypatch, tmp_path):
    import morning_news

    delivered = {}
    monkeypatch.setattr(morning_news, "build_notice_epub",
                        lambda settings, reason, output_dir=None: tmp_path / "notice.epub")
    monkeypatch.setattr(morning_news, "upload_to_drive", lambda path: delivered.setdefault("path", path))
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "folder")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        morning_news.run_edition(settings())
    assert delivered["path"].name == "notice.epub"


def test_failure_notice_never_overwrites_a_real_edition(tmp_path):
    """A failing midday run must not replace the paper that went out at 05:30."""
    from newspaper import build_notice_epub
    from drive_delivery import PATTERN
    article = {"title": "Historie", "source": "Kilde", "url": "https://example.test", "summary": "Kort",
               "body": "<div><p>Tekst</p></div>", "why": "Relevant", "reading_minutes": 1}
    real = build_epub([article], settings(), tmp_path)
    notice = build_notice_epub(settings(), "Kun 3 læsbare artikler", tmp_path)
    assert real.name != notice.name
    # Drive retention must still recognise the notice, or it would never be pruned.
    assert PATTERN.fullmatch(notice.name) and PATTERN.fullmatch(real.name)


def test_selection_retry_skips_errors_that_cannot_succeed(monkeypatch):
    """Retrying a too-long prompt costs 20 seconds and a second bill for nothing."""
    from morning_news import ai_call_with_retry
    calls = []

    def failing(prompt, configured, search=False):
        calls.append(prompt)
        raise ValueError("AI prompt exceeds configured character budget")

    monkeypatch.setattr("morning_news.ai_call", failing)
    with pytest.raises(ValueError):
        ai_call_with_retry("p", settings())
    assert len(calls) == 1


def test_selection_retry_covers_a_transient_error(monkeypatch):
    from openai import APITimeoutError
    from morning_news import ai_call_with_retry
    attempts = []

    def flaky(prompt, configured, search=False):
        attempts.append(prompt)
        if len(attempts) == 1:
            raise APITimeoutError(request=None)
        return {"selected": []}

    monkeypatch.setattr("morning_news.ai_call", flaky)
    monkeypatch.setattr("morning_news.time.sleep", lambda seconds: None)
    assert ai_call_with_retry("p", settings()) == {"selected": []}
    assert len(attempts) == 2
