from datetime import datetime, timezone

from curation import ranked_shortlist, take_replacement, mix_report
from morning_news import load_settings


def article(i, **extra):
    return dict(title=f"Historie {i}", url=f"https://source.test/{i}", source="Kilde",
                summary="", **extra)


def test_relevant_late_article_reaches_editor_and_discovery_is_preserved():
    settings = load_settings()
    settings["edition"]["ai_shortlist_size"] = 5
    items = [article(i) for i in range(20)]
    items[-1].update(title="Regeringen indgår forlig om ny finanslov", summary="Konkret baggrund " * 30)
    chosen = ranked_shortlist(items, settings, now=datetime(2026, 9, 14, tzinfo=timezone.utc))
    assert items[-1] in chosen
    assert len(chosen) == 5
    assert len({a["url"] for a in chosen}) == 5
    assert any(a["summary"] == "" for a in chosen)


def test_shortlist_keeps_web_reserve_and_does_not_mutate_candidates():
    settings = load_settings()
    settings["edition"]["ai_shortlist_size"] = 9
    items = [article(i, pool="rss") for i in range(20)]
    items += [article(i, pool="web") for i in range(20, 24)]
    chosen = ranked_shortlist(items, settings)
    assert sum(a["pool"] == "web" for a in chosen) >= 3
    assert len(items) == 24


def test_political_reserve_beats_first_technology_reserve():
    missing = article(1, editorial_topic="dansk_politik", format="longread")
    backups = [article(2, editorial_topic="teknologi", format="longread"),
               article(3, editorial_topic="dansk_politik", format="short"),
               article(4, editorial_topic="dansk_politik", format="longread")]
    assert take_replacement(backups, missing)["url"].endswith("/4")
    assert take_replacement(backups, missing)["url"].endswith("/3")
    assert take_replacement(backups, missing)["url"].endswith("/2")
    assert take_replacement(backups, missing) is None


def test_lost_politics_and_depth_are_reported_without_forcing_filler():
    missing = article(1, editorial_topic="dansk_politik", format="longread")
    backup = article(2, editorial_topic="teknologi", format="short", is_backup=True)
    report = mix_report([missing, backup], [backup])
    assert len(report["warnings"]) == 2
    assert report["planned"] == {"dansk_politik": 1}


def test_pipeline_replaces_blocked_politics_before_filling_with_technology(monkeypatch, tmp_path):
    import morning_news as news
    settings = load_settings()
    settings["edition"].update(max_articles=1, minimum_articles=1)
    missing = article(1, editorial_topic="dansk_politik", format="longread")
    tech = article(2, editorial_topic="teknologi", format="short", is_backup=True)
    politics = article(3, editorial_topic="dansk_politik", format="longread", is_backup=True)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.delenv("GOOGLE_DRIVE_FOLDER_ID", raising=False)
    monkeypatch.setattr(news, "fetch_candidates", lambda *a: [missing])
    monkeypatch.setattr(news, "fetch_web_candidates", lambda *a: [])
    monkeypatch.setattr(news, "published_history", lambda *a: {})
    monkeypatch.setattr(news, "enrich_candidate_previews", lambda *a: ([missing], 0, False))
    monkeypatch.setattr(news, "select_articles", lambda *a: [missing, tech, politics])
    attempts = []
    def prepare(a, reader):
        attempts.append(a["url"])
        return None if a == missing else dict(a, body="Readable article")
    monkeypatch.setattr(news, "prepare_article", prepare)
    monkeypatch.setattr(news, "build_epub", lambda *a, **kw: tmp_path / "edition.epub")
    monkeypatch.setattr(news, "editorial_note", lambda: "Note")
    monkeypatch.setattr(news, "record_edition", lambda *a: True)
    result = news._run_edition(settings)
    assert attempts == [missing["url"], politics["url"]]
    assert result["article_list"][0]["url"] == politics["url"]
    assert result["report"]["editorial_mix"]["warnings"] == []
