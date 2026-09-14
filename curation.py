"""Local candidate triage and replacement matching; no model or network calls."""
import re
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from editorial import dedupe, fair_sample


TOPICS = ("dansk_politik", "kultur", "teknologi", "verden", "fordybelse")
LABELS = dict(zip(TOPICS, ("dansk politik og samfund", "kultur", "teknologi",
                          "verden og sikkerhed", "fordybelse og personlige interesser")))


def matches(text, phrase):
    return bool(re.search(r"(?<!\w)" + re.escape(str(phrase).lower()) + r"(?!\w)", text))


def candidate_score(article, config, now):
    text = (article.get("title", "") + " " + article.get("summary", "")).lower()
    # Cap repeated keywords: mentioning Apple ten times must not win ten times.
    interest = max((min(3, sum(matches(text, word) for word in words))
                    for words in config.get("interest_signals", {}).values()), default=0)
    context = min(1.5, len(article.get("summary", "").split()) / 40)
    recency = 0
    value = article.get("published")
    if value:
        try:
            try:
                stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                stamp = parsedate_to_datetime(str(value))
            stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
            age = (now - stamp).total_seconds() / 86400
            if 0 <= age <= 3:
                recency = 1 - age / 3
        except (ValueError, TypeError, OverflowError):
            pass
    return interest * 2 + context + recency


def ranked_shortlist(candidates, settings, now=None):
    cap = int(settings["edition"].get("ai_shortlist_size", 120))
    candidates = dedupe(candidates)
    if len(candidates) <= cap:
        return candidates
    now = now or datetime.now(timezone.utc)
    config = settings.get("curation", {})
    ranked = sorted(candidates, key=lambda a: candidate_score(a, config, now), reverse=True)
    # Reserve one fifth for source-balanced discovery outside keyword ranking.
    discovery_count = max(1, cap // 5)
    core = fair_sample(ranked, cap - discovery_count)
    # Preserve existing room for independently discovered web stories.
    web = fair_sample([a for a in ranked if a.get("pool") == "web"], min(cap // 3, 45))
    chosen = dedupe(web + core)[:cap - discovery_count]
    urls = {a["url"] for a in chosen}
    remainder = [a for a in candidates if a["url"] not in urls]
    # Rotate discovery daily so permanent feed ordering does not hide surprises.
    if remainder:
        offset = now.date().toordinal() % len(remainder)
        remainder = remainder[offset:] + remainder[:offset]
    return chosen + fair_sample(remainder, cap - len(chosen))


def replacement_topic(article):
    declared = article.get("editorial_topic")
    if declared in TOPICS:
        return declared
    # Compatibility for archived editions / models omitting the new field.
    section = article.get("section", "").lower()
    if any(matches(section, word) for word in ("politik", "danmark", "samfund", "økonomi")):
        return "dansk_politik"
    if "kultur" in section:
        return "kultur"
    return {"Danmark og kultur": "kultur", "Teknologi og verden": "teknologi"}.get(
        article.get("group"), "fordybelse")


def take_replacement(backups, missing):
    """Use a same-topic reserve first, then a longread if depth was lost."""
    if not backups:
        return None
    def fit(article):
        return (replacement_topic(article) == replacement_topic(missing),
                article.get("format") == missing.get("format"))
    index = max(range(len(backups)), key=lambda i: fit(backups[i]))
    return backups.pop(index)


def mix_report(approved, prepared):
    planned = Counter(replacement_topic(a) for a in approved if not a.get("is_backup"))
    actual = Counter(replacement_topic(a) for a in prepared)
    warnings = [f"Færre historier om {LABELS[topic]} end planlagt ({actual[topic]} af {count})."
                for topic, count in planned.items() if actual[topic] < count]
    planned_depth = sum(a.get("format") == "longread" for a in approved if not a.get("is_backup"))
    depth = sum(a.get("format") == "longread" for a in prepared)
    if depth < planned_depth:
        warnings.append(f"Færre longreads end planlagt ({depth} af {planned_depth}).")
    return {"planned": dict(planned), "actual": dict(actual), "warnings": warnings}
