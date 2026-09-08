"""GitHub-backed edition and feedback storage.

The feedback branch is deliberately separate from main: clicks do not redeploy
the Render service, yet remain portable, editable, and versioned in GitHub.
"""
import base64
import json
import os
from datetime import datetime, timedelta, timezone

import requests


API = "https://api.github.com"
LATEST_PATH = "feedback/latest_edition.json"
EVENTS_PATH = "feedback/events.jsonl"
EDITIONS_PREFIX = "feedback/editions"
RETENTION_DAYS = 10


def branch():
    return os.environ.get("GITHUB_FEEDBACK_BRANCH", "feedback-data")


def configured():
    return bool(os.environ.get("GITHUB_FEEDBACK_TOKEN") and os.environ.get("GITHUB_REPOSITORY"))


def _headers():
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {os.environ['GITHUB_FEEDBACK_TOKEN']}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _url(path):
    return f"{API}/repos/{os.environ['GITHUB_REPOSITORY']}/contents/{path}"


def _ensure_branch():
    """Create the feedback branch from main when the token is first used."""
    repository = os.environ["GITHUB_REPOSITORY"]
    headers = _headers()
    existing = requests.get(f"{API}/repos/{repository}/git/ref/heads/{branch()}", headers=headers, timeout=12)
    if existing.status_code == 200:
        return
    if existing.status_code != 404:
        existing.raise_for_status()
    main = requests.get(f"{API}/repos/{repository}/git/ref/heads/main", headers=headers, timeout=12)
    main.raise_for_status()
    created = requests.post(
        f"{API}/repos/{repository}/git/refs", headers=headers, timeout=12,
        json={"ref": f"refs/heads/{branch()}", "sha": main.json()["object"]["sha"]},
    )
    if created.status_code not in (201, 422):
        created.raise_for_status()


def _read(path):
    if not configured():
        return None, None
    response = requests.get(_url(path), headers=_headers(), params={"ref": branch()}, timeout=12)
    if response.status_code == 404:
        return None, None
    response.raise_for_status()
    payload = response.json()
    return base64.b64decode(payload["content"]).decode("utf-8"), payload["sha"]


def _write(path, text, message):
    _ensure_branch()
    _, sha = _read(path)
    payload = {
        "message": message,
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "branch": branch(),
    }
    if sha:
        payload["sha"] = sha
    response = requests.put(_url(path), headers=_headers(), json=payload, timeout=15)
    response.raise_for_status()


def _delete(path, message):
    """Delete an old edition once it is outside the ten-day retention window."""
    _ensure_branch()
    _, sha = _read(path)
    if not sha:
        return
    response = requests.delete(
        _url(path), headers=_headers(), timeout=15,
        json={"message": message, "sha": sha, "branch": branch()},
    )
    response.raise_for_status()


def _prune_editions():
    repository = os.environ["GITHUB_REPOSITORY"]
    response = requests.get(
        f"{API}/repos/{repository}/contents/{EDITIONS_PREFIX}", headers=_headers(),
        params={"ref": branch()}, timeout=12,
    )
    if response.status_code == 404:
        return
    response.raise_for_status()
    editions = sorted(item["path"] for item in response.json() if item.get("name", "").endswith(".json"))
    for path in editions[:-RETENTION_DAYS]:
        _delete(path, "Remove expired Mads Morgen edition")


def record_edition(articles, report=None):
    """Store selected metadata; article bodies never enter GitHub."""
    if not configured():
        return False
    created_at = datetime.now(timezone.utc)
    edition = {
        "created_at": created_at.isoformat(),
        "articles": [
            {key: str(article.get(key, ""))[:500] for key in (
                "title", "source", "url", "summary", "format", "section", "why", "reading_minutes"
            )}
            for article in articles
        ],
    }
    if report:
        edition["report"] = report
    encoded = json.dumps(edition, ensure_ascii=False, indent=2) + "\n"
    _write(LATEST_PATH, encoded, "Record latest Mads Morgen edition")
    _write(f"{EDITIONS_PREFIX}/{created_at.date().isoformat()}.json", encoded, "Archive Mads Morgen edition")
    _prune_editions()
    return True


def latest_edition():
    text, _ = _read(LATEST_PATH)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def add_feedback(article, direction):
    """Append an explicit preference to a compact, versioned JSONL event log."""
    if direction not in {"more", "less"}:
        raise ValueError("Feedback must be 'more' or 'less'")
    valid_reasons = {"", "great_match", "great_depth", "surprising", "uninteresting",
                     "good_but_too_technical", "too_thin", "too_long", "too_promotional",
                     "too_old", "duplicate"}
    if article.get("reason", "") not in valid_reasons:
        raise ValueError("Unknown feedback reason")
    if not configured():
        return False
    existing, _ = _read(EVENTS_PATH)
    event = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "direction": direction,
        "title": str(article.get("title", ""))[:300],
        "source": str(article.get("source", ""))[:160],
        "url": str(article.get("url", ""))[:500],
        "summary": str(article.get("summary", ""))[:500],
        "reason": str(article.get("reason", ""))[:80],
    }
    lines = (existing or "").splitlines()[-999:]
    lines.append(json.dumps(event, ensure_ascii=False))
    _write(EVENTS_PATH, "\n".join(lines) + "\n", f"Record feedback: {direction}")
    return True


def recent_feedback(limit=30):
    """Return compact signals for the next editorial selection."""
    text, _ = _read(EVENTS_PATH)
    if not text:
        return []
    events = []
    for line in text.splitlines()[-limit:]:
        try:
            event = json.loads(line)
            if event.get("direction") in {"more", "less"}:
                events.append({key: event.get(key, "") for key in ("created_at", "direction", "title", "source", "summary", "reason")})
        except json.JSONDecodeError:
            continue
    return events


def editorial_note(days=7):
    """A transparent, non-AI weekly note based only on explicit clicks."""
    now = datetime.now(timezone.utc)
    recent = []
    for event in recent_feedback(limit=200):
        try:
            created = datetime.fromisoformat(event.get("created_at", "").replace("Z", "+00:00"))
            if now - created <= timedelta(days=days):
                recent.append(event)
        except (TypeError, ValueError):
            continue
    if not recent:
        return "Ingen feedback den seneste uge endnu. Avisen følger den faste redaktionelle profil."
    more = sum(item["direction"] == "more" for item in recent)
    less = len(recent) - more
    reasons = {}
    for item in recent:
        reason = item.get("reason", "")
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    labels = {
        "great_match": "godt emne og vinkel", "great_depth": "god dybde", "surprising": "overraskende fund",
        "uninteresting": "uinteressant", "good_but_too_technical": "godt, men for nørdet",
        "too_thin": "for tyndt", "too_long": "for langt", "too_promotional": "for meget PR",
        "too_old": "for gammelt", "duplicate": "gentagelse",
    }
    strongest = max(reasons, key=reasons.get) if reasons else ""
    ending = f" Det hyppigste signal var: {labels.get(strongest, strongest)}." if strongest else ""
    return f"Ugens feedback: {more} 'mere' og {less} 'mindre'. Det bruges som et signal — ikke som en hård regel for hele emner." + ending
