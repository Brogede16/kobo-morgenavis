from pathlib import Path

from morning_news import build_epub, editorial_prompt_profile, enforce_source_diversity, load_settings, select_articles


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
    assert len(select_articles(candidates, settings())) == 2


def test_build_epub(tmp_path, monkeypatch):
    monkeypatch.setattr("morning_news.article_body", lambda url, fallback: "<p>Tekst</p>")
    path = build_epub([{"title": "Historie", "source": "Kilde", "url": "https://example.test", "summary": "Kort"}], settings(), tmp_path)
    assert path.exists() and path.suffix == ".epub"


def test_editorial_prompt_profile_caps_examples(monkeypatch, tmp_path):
    profile = tmp_path / "editorial.yaml"
    profile.write_text("editorial: {voice: Calm}\nexamples:\n  read:\n" + "\n".join(f"    - url: https://x/{i}\n      reason: useful" for i in range(15)))
    monkeypatch.setattr("morning_news.load_editorial_profile", lambda: __import__("yaml").safe_load(profile.read_text()))
    assert len(editorial_prompt_profile()["examples"]["read"]) == 12


def test_source_diversity_caps_a_single_outlet():
    items = [{"title": str(i), "source": "One", "url": f"https://one/{i}", "summary": ""} for i in range(3)]
    items += [{"title": "other", "source": "Two", "url": "https://two/1", "summary": ""}]
    configured = settings()
    configured["edition"]["max_articles"] = 3
    configured["edition"]["max_articles_per_source"] = 2
    assert [item["source"] for item in enforce_source_diversity(items, items, configured)] == ["One", "One", "Two"]
