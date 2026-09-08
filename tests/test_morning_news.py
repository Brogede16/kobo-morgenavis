from pathlib import Path

import pytest

from feedback_store import editorial_note
from morning_news import build_epub, editorial_prompt_profile, enforce_source_diversity, load_settings, merge_candidate_pools, prune_old_drive_editions, select_articles
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
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    candidates = [{"title": str(i), "source": "x", "url": f"https://x/{i}", "summary": "s"} for i in range(3)]
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
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


def test_editorial_prompt_profile_caps_examples(monkeypatch, tmp_path):
    profile = tmp_path / "editorial.yaml"
    profile.write_text("editorial: {voice: Calm}\nexamples:\n  read:\n" + "\n".join(f"    - url: https://x/{i}\n      reason: useful" for i in range(15)))
    monkeypatch.setattr("morning_news.load_editorial_profile", lambda: __import__("yaml").safe_load(profile.read_text()))
    assert len(editorial_prompt_profile()["examples"]["read"]) == 4


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
