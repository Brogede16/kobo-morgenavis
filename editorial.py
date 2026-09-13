"""Compact editorial context, source-fair sampling and conservative selection."""
import json
import re
from collections import OrderedDict
from datetime import datetime, timezone
from urllib.parse import urlsplit

from news_io import canonical_url


def text_value(value):
    # Older YAML used unquoted colons, making some prose list items mappings.
    if isinstance(value, dict):
        return "; ".join(f"{k}: {text_value(v)}" for k, v in value.items())
    return str(value)


def terms(text):
    return set(re.findall(r"[\wæøå]{3,}", text.lower())) - {
        "the", "and", "for", "with", "this", "that", "from", "har", "som", "det", "til", "med"}


def fair_sample(items, limit):
    groups = OrderedDict()
    for item in items:
        key = item.get("source") or urlsplit(item.get("url", "")).hostname or "unknown"
        groups.setdefault(key, []).append(item)
    result = []
    while groups and len(result) < limit:
        for key in list(groups):
            result.append(groups[key].pop(0))
            if not groups[key]:
                del groups[key]
            if len(result) == limit:
                break
    return result


# Everything in editorial_profile.yaml reaches the editor except the keys below.
# A denylist means a new policy added to the profile is used immediately; the old
# allowlist silently dropped any key someone forgot to register here.
PROFILE_SKIP_KEYS = {"priorities"}  # sent separately as rotating relevant_interests


def compact_profile(profile, candidates=(), max_chars=16000):
    editorial = profile.get("editorial", {})
    brief = {key: value for key, value in editorial.items() if key not in PROFILE_SKIP_KEYS}
    # Keep every interest in Git. Include relevant detail plus rotating discovery interests.
    priorities = [text_value(p) for p in editorial.get("priorities", [])]
    relevant = terms(" ".join(c.get("title", "") + " " + c.get("summary", "") for c in candidates))
    ordered = sorted(priorities, key=lambda p: len(terms(p) & relevant), reverse=True)
    if priorities:
        offset = datetime.now(timezone.utc).date().toordinal() % len(priorities)
        rotated = priorities[offset:] + priorities[:offset]
    else:
        rotated = []
    brief["relevant_interests"] = list(dict.fromkeys(ordered[:10] + rotated[:6]))
    result = {"editorial": brief, "examples": {}}
    for direction in ("read", "skip"):
        entries = [e for e in profile.get("examples", {}).get(direction, []) if isinstance(e, dict)]
        entries.sort(key=lambda e: len(terms(e.get("reason", "") + " " + e.get("url", "")) & relevant), reverse=True)
        result["examples"][direction] = [{"url": str(e.get("url", ""))[:240],
                                           "reason": str(e.get("reason", ""))[:220]} for e in entries[:8]]
    # Remove optional detail first; never cut a rule halfway through a sentence.
    while len(json.dumps(result, ensure_ascii=False)) > max_chars:
        removable = result["editorial"].get("relevant_interests", [])
        if removable:
            removable.pop()
        elif result["examples"].get("skip"):
            result["examples"]["skip"].pop()
        elif result["examples"].get("read"):
            result["examples"]["read"].pop()
        else:
            raise ValueError("Core editorial rules exceed configured prompt budget")
    return result


def near_duplicate(title_words, other_words, shared=3, ratio=.7):
    """True when two headlines clearly describe the same story."""
    overlap = len(title_words & other_words)
    if overlap < shared:
        return False
    return overlap / max(1, min(len(title_words), len(other_words))) >= ratio


def drop_already_published(candidates, history):
    """Remove candidates that recent editions already carried.

    `history` comes from feedback_store.published_history(): the URLs, story ids
    and headlines of the last few editions. Without it the paper has no memory
    and reprints the same story from a different outlet the next morning.
    """
    if not history:
        return candidates
    urls = set(history.get("urls", ()))
    stories = {value for value in history.get("story_ids", ()) if value}
    titles = [terms(value) for value in history.get("titles", ())]
    kept = []
    for item in candidates:
        if canonical_url(item.get("url", "")) in urls:
            continue
        if item.get("story_id") and item["story_id"] in stories:
            continue
        words = terms(item.get("title", ""))
        if words and any(near_duplicate(words, old) for old in titles):
            continue
        kept.append(item)
    return kept


def dedupe(items):
    result, seen = [], set()
    for item in items:
        url = canonical_url(item.get("url", ""))
        if url and url not in seen:
            result.append(dict(item, url=url))
            seen.add(url)
    return result


def diverse_selection(selected, settings):
    """Filter only approved selections. Never backfill with rejected articles."""
    limit = int(settings["edition"]["max_articles"])
    per_source = int(settings["edition"].get("max_articles_per_source", 2))
    limits = settings.get("source_limits", {})
    result, counts, seen_stories = [], {}, set()
    for item in dedupe(selected):
        source = (urlsplit(item["url"]).hostname or item.get("source", "")).removeprefix("www.")
        cap = int(limits.get(source, limits.get(item.get("source"), per_source)))
        story_id = item.get("story_id")
        title_words = terms(item.get("title", ""))
        repeat = any(near_duplicate(title_words, terms(old["title"])) for old in result)
        if counts.get(source, 0) >= cap or repeat or (story_id and story_id in seen_stories):
            continue
        result.append(item)
        counts[source] = counts.get(source, 0) + 1
        if story_id:
            seen_stories.add(story_id)
        if len(result) >= limit:
            break
    return result
