"""Fetch, select, render, and deliver a daily EPUB edition."""
import base64
import html
import json
import logging
import os
import re
from datetime import date
from pathlib import Path

import feedparser
import yaml
from ebooklib import epub
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google import genai
from google.genai import types
from readability import Document
import requests

logger = logging.getLogger(__name__)
ROOT = Path(__file__).parent


def load_settings(path=ROOT / "sources.yaml"):
    with open(path, encoding="utf-8") as file:
        data = yaml.safe_load(file)
    required = {"title", "language", "max_articles", "timezone", "schedule", "topics"}
    missing = required - set(data.get("edition", {}))
    if missing or not data.get("sources"):
        raise ValueError(f"Invalid sources.yaml; missing edition keys: {sorted(missing)}")
    return data


def load_editorial_profile(path=ROOT / "editorial_profile.yaml"):
    with open(path, encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def fetch_candidates(settings):
    candidates = []
    for source in settings["sources"]:
        if not source.get("enabled", True):
            continue
        feed = feedparser.parse(source["url"])
        if feed.bozo and not feed.entries:
            logger.warning("Could not parse feed %s", source["name"])
            continue
        for entry in feed.entries[:int(settings.get("collection", {}).get("max_items_per_source", 25))]:
            link = entry.get("link")
            title = entry.get("title", "").strip()
            if link and title:
                candidates.append({
                    "source": source["name"], "title": title,
                    "url": link, "summary": re.sub("<[^>]+>", "", entry.get("summary", ""))[:700],
                    "format": source.get("format", "mixed"),
                })
    # An article syndicated in several feeds should appear once.
    return list({item["url"]: item for item in candidates}.values())[:settings["edition"]["max_candidates"]]


def fetch_web_candidates(settings):
    """Use at most a couple of search queries; disabled by default to control spend."""
    config = settings.get("web_search", {})
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not config.get("queries"):
        return []
    queries = config["queries"][:int(config.get("max_queries", 2))]
    prompt = ("Find aktuelle, troværdige nyhedsartikler for disse søgninger: " + json.dumps(queries, ensure_ascii=False) +
              '. Return ONLY JSON: {"articles":[{"title":"...","url":"https://...","source":"...","summary":"max 240 chars"}]}. '
              "Return at most 8 articles total; only direct article URLs.")
    response = genai.Client(api_key=api_key).models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"), contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            response_mime_type="application/json", max_output_tokens=700,
        ),
    )
    try:
        articles = json.loads(response.text).get("articles", [])
        return [{"title": a["title"], "url": a["url"], "source": a.get("source", "Web"),
                 "summary": a.get("summary", "")[:240]} for a in articles
                if isinstance(a, dict) and a.get("title") and str(a.get("url", "")).startswith("https://")]
    except (ValueError, TypeError, KeyError) as exc:
        logger.warning("Web search response malformed (%s)", exc)
        return []


def shortlist_candidates(candidates, settings):
    """Local, free triage keeps a large source collection out of the AI prompt."""
    topics = " ".join(settings["edition"]["topics"]).lower()
    def score(item):
        text = (item["title"] + " " + item["summary"]).lower()
        return sum(term in text for term in topics.split()) + len(item["summary"]) / 10000
    return sorted(candidates, key=score, reverse=True)[:int(settings["edition"].get("ai_shortlist_size", 80))]


def select_articles(candidates, settings):
    limit = int(settings["edition"]["max_articles"])
    if not candidates:
        return []
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY absent; selecting newest feed items without AI")
        return candidates[:limit]
    candidates = shortlist_candidates(candidates, settings)
    maximum = int(settings["edition"].get("max_summary_characters", 260))
    compact = [{"i": i, "title": c["title"][:160], "source": c["source"], "format": c.get("format", "mixed"),
                "summary": c["summary"][:maximum]} for i, c in enumerate(candidates)]
    profile = load_editorial_profile()
    prompt = (
        "You are the editor of a Danish morning newspaper. Pick the most useful, varied "
        f"{limit} articles for topics {settings['edition']['topics']}. Aim for the reader's desired mix, "
        "with both quick essential updates and 2-3 substantive longreads/analyses. Avoid promotional, "
        "podcast-launch, giveaway, and advertising stories. Use these personal preferences and labelled examples: "
        + json.dumps(profile, ensure_ascii=False) + ". Return ONLY JSON: "
        '{"selected":[integer indexes]}. Do not include more than the limit. Candidates: '
        + json.dumps(compact, ensure_ascii=False)
    )
    response = genai.Client(api_key=api_key).models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"), contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", max_output_tokens=400),
    )
    try:
        indexes = json.loads(response.text)["selected"]
        return [candidates[i] for i in indexes if isinstance(i, int) and 0 <= i < len(candidates)][:limit]
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning("AI selection malformed (%s); using feed order", exc)
        return candidates[:limit]


def article_body(url, fallback):
    try:
        response = requests.get(url, timeout=15, headers={"User-Agent": "MadsMorgen/1.0 (+personal reader)"})
        response.raise_for_status()
        content = Document(response.text).summary(html_partial=True)
        # Preserve only simple safe markup for EPUB readers.
        return content if len(content) > 200 else f"<p>{html.escape(fallback)}</p>"
    except requests.RequestException:
        logger.warning("Could not fetch %s", url)
        return f"<p>{html.escape(fallback)}</p>"


def hero_image(url, max_width, max_bytes):
    """Download one small EPUB-safe Open Graph image (no image-processing dependency)."""
    try:
        page = requests.get(url, timeout=15, headers={"User-Agent": "MadsMorgen/1.0 (+personal reader)"})
        page.raise_for_status()
        match = re.search(r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)', page.text, re.I)
        if not match:
            return None
        image_response = requests.get(match.group(1), timeout=15, stream=True, headers={"User-Agent": "MadsMorgen/1.0"})
        image_response.raise_for_status()
        mime_type = image_response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        formats = {"image/jpeg": "jpg", "image/png": "png"}
        if mime_type not in formats:
            return None
        raw = image_response.raw.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return None
        return raw, formats[mime_type], mime_type
    except Exception as exc:
        logger.info("Skipping hero image for %s (%s)", url, exc)
        return None


def build_epub(articles, settings, output_dir=ROOT / "output"):
    output_dir.mkdir(exist_ok=True)
    today = date.today().isoformat()
    book = epub.EpubBook()
    title = f"{settings['edition']['title']} — {today}"
    book.set_identifier(f"mads-morgen-{today}")
    book.set_title(title)
    book.set_language(settings["edition"]["language"])
    book.add_author("Mads Morgen")
    intro = epub.EpubHtml(title="Forside", file_name="index.xhtml", lang="da")
    intro.content = f"<h1>{html.escape(title)}</h1><p>{len(articles)} udvalgte historier.</p>"
    book.add_item(intro)
    chapters = [intro]
    for number, article in enumerate(articles, start=1):
        chapter = epub.EpubHtml(title=article["title"], file_name=f"article-{number}.xhtml", lang="da")
        image_markup = ""
        image_settings = settings.get("images", {})
        if image_settings.get("enabled", False):
            image = hero_image(article["url"], int(image_settings.get("max_width", 1200)), int(image_settings.get("max_bytes", 2500000)))
            if image:
                raw, extension, mime_type = image
                filename = f"images/article-{number}.{extension}"
                book.add_item(epub.EpubItem(uid=f"image-{number}", file_name=filename, media_type=mime_type, content=raw))
                image_markup = f'<p><img src="{filename}" alt="" /></p>'
        chapter.content = (f"<h1>{html.escape(article['title'])}</h1><p><em>{html.escape(article['source'])}</em></p>{image_markup}"
                           f"{article_body(article['url'], article['summary'])}<p><a href=\"{html.escape(article['url'])}\">Læs originalen</a></p>")
        book.add_item(chapter)
        chapters.append(chapter)
    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *chapters]
    path = output_dir / f"mads-morgen-{today}.epub"
    epub.write_epub(str(path), book)
    return path


def upload_to_drive(path):
    folder_id = os.environ["GOOGLE_DRIVE_FOLDER_ID"]
    if os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN"):
        credentials = Credentials(
            token=None, refresh_token=os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )
    else:
        encoded = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON_B64"]
        info = json.loads(base64.b64decode(encoded))
        credentials = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/drive.file"])
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    # Same name is deliberate: Kobo's Google Drive import sees today's current edition.
    existing = drive.files().list(q=f"'{folder_id}' in parents and name='{path.name}' and trashed=false",
                                  fields="files(id)").execute().get("files", [])
    metadata = {"name": path.name, "parents": [folder_id], "mimeType": "application/epub+zip"}
    media = MediaFileUpload(str(path), mimetype="application/epub+zip", resumable=True)
    if existing:
        return drive.files().update(fileId=existing[0]["id"], body=metadata, media_body=media, fields="id,name").execute()
    return drive.files().create(body=metadata, media_body=media, fields="id,name").execute()


def run_edition(settings=None, use_web_search=None):
    settings = settings or load_settings()
    candidates = fetch_candidates(settings)
    web_config = settings.get("web_search", {})
    if use_web_search if use_web_search is not None else web_config.get("enabled_for_schedule", False):
        candidates.extend(fetch_web_candidates(settings))
        candidates = list({item["url"]: item for item in candidates}.values())[:settings["edition"]["max_candidates"]]
    articles = select_articles(candidates, settings)
    if not articles:
        raise RuntimeError("No articles found; EPUB was not generated")
    path = build_epub(articles, settings)
    uploaded = upload_to_drive(path) if os.environ.get("GOOGLE_DRIVE_FOLDER_ID") else None
    logger.info("Edition complete: %s (%s articles)%s", path, len(articles), " uploaded" if uploaded else "")
    return {"path": str(path), "articles": len(articles), "drive_file": uploaded}
