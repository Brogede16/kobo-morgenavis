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


def compact_profile(profile, candidates=(), max_chars=11500):
    editorial = profile.get("editorial", {})
    keys = ("voice", "desired_mix", "daily_structure", "priority_tiers", "exclusions",
            "source_policy", "source_roles", "paywall_policy", "readability_policy",
            "daily_readiness_test", "film_policy", "culture_policy", "gaming_policy",
            "photography_source_policy", "sexuality_policy")
    brief = {key: editorial[key] for key in keys if key in editorial}
    # Keep every interest in Git. Include relevant detail plus rotating discovery interests.
    priorities = [text_value(p) for p in editorial.get("priorities", [])]
    relevant = terms(" ".join(c.get("title", "") + " " + c.get("summary", "") for c in candidates))
    ordered = sorted(priorities, key=lambda p: len(terms(p) & relevant), reverse=True)
    if priorities:
        offset = datetime.now(timezone.utc).date().toordinal() % len(priorities)
        rotated = priorities[offset:] + priorities[:offset]
    else:
        rotated = []
    brief["relevant_interests"] = list(dict.fromkeys(ordered[:8] + rotated[:4]))
    result = {"editorial": brief, "examples": {}}
    for direction in ("read", "skip"):
        entries = [e for e in profile.get("examples", {}).get(direction, []) if isinstance(e, dict)]
        entries.sort(key=lambda e: len(terms(e.get("reason", "") + " " + e.get("url", "")) & relevant), reverse=True)
        result["examples"][direction] = [{"url": str(e.get("url", ""))[:240],
                                           "reason": str(e.get("reason", ""))[:220]} for e in entries[:4]]
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
        near_duplicate = any(len(title_words & terms(old["title"])) >= 3 and
                             len(title_words & terms(old["title"])) / max(1, min(len(title_words), len(terms(old["title"])))) >= .7
                             for old in result)
        if counts.get(source, 0) >= cap or near_duplicate or (story_id and story_id in seen_stories):
            continue
        result.append(item)
        counts[source] = counts.get(source, 0) + 1
        if story_id:
            seen_stories.add(story_id)
        if len(result) >= limit:
            break
    return result
