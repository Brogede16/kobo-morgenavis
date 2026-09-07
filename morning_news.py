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
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from openai import OpenAI
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


def fetch_candidates(settings):
    candidates = []
    for source in settings["sources"]:
        if not source.get("enabled", True):
            continue
        feed = feedparser.parse(source["url"])
        if feed.bozo and not feed.entries:
            logger.warning("Could not parse feed %s", source["name"])
            continue
        for entry in feed.entries:
            link = entry.get("link")
            title = entry.get("title", "").strip()
            if link and title:
                candidates.append({
                    "source": source["name"], "title": title,
                    "url": link, "summary": re.sub("<[^>]+>", "", entry.get("summary", ""))[:700],
                })
    # An article syndicated in several feeds should appear once.
    return list({item["url"]: item for item in candidates}.values())


def select_articles(candidates, settings):
    limit = int(settings["edition"]["max_articles"])
    if not candidates:
        return []
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY absent; selecting newest feed items without AI")
        return candidates[:limit]
    compact = [{"i": i, "title": c["title"], "source": c["source"], "summary": c["summary"]} for i, c in enumerate(candidates)]
    prompt = (
        "You are the editor of a Danish morning newspaper. Pick the most useful, varied "
        f"{limit} articles for topics {settings['edition']['topics']}. Return ONLY JSON: "
        '{"selected":[integer indexes]}. Do not include more than the limit. Candidates: '
        + json.dumps(compact, ensure_ascii=False)
    )
    response = OpenAI(api_key=api_key).responses.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-5-mini"), input=prompt, store=False,
        text={"format": {"type": "json_object"}}, max_output_tokens=500,
    )
    try:
        indexes = json.loads(response.output_text)["selected"]
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
        chapter.content = (f"<h1>{html.escape(article['title'])}</h1><p><em>{html.escape(article['source'])}</em></p>"
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
    encoded = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON_B64"]
    info = json.loads(base64.b64decode(encoded))
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.file"])
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    # Same name is deliberate: Kobo's Google Drive import sees today's current edition.
    existing = drive.files().list(q=f"'{folder_id}' in parents and name='{path.name}' and trashed=false",
                                  fields="files(id)").execute().get("files", [])
    metadata = {"name": path.name, "parents": [folder_id], "mimeType": "application/epub+zip"}
    media = MediaFileUpload(str(path), mimetype="application/epub+zip", resumable=True)
    if existing:
        return drive.files().update(fileId=existing[0]["id"], body=metadata, media_body=media, fields="id,name").execute()
    return drive.files().create(body=metadata, media_body=media, fields="id,name").execute()


def run_edition(settings=None):
    settings = settings or load_settings()
    candidates = fetch_candidates(settings)
    articles = select_articles(candidates, settings)
    if not articles:
        raise RuntimeError("No articles found; EPUB was not generated")
    path = build_epub(articles, settings)
    uploaded = upload_to_drive(path) if os.environ.get("GOOGLE_DRIVE_FOLDER_ID") else None
    logger.info("Edition complete: %s (%s articles)%s", path, len(articles), " uploaded" if uploaded else "")
    return {"path": str(path), "articles": len(articles), "drive_file": uploaded}

