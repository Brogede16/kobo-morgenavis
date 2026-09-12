"""Offline, reflowable EPUB in the approved Mads Morgen layout."""
import html
import io
import logging
import math
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from ebooklib import epub
from lxml import etree, html as lxml_html
from PIL import Image, ImageOps
from readability import Document
import requests

from news_io import WebReader, canonical_url

logger = logging.getLogger(__name__)
STYLE = """
body {font-family:serif;line-height:1.5;margin:5%;color:#18201c}
h1,h2 {font-family:sans-serif;line-height:1.2;color:#123f38}
h1 {font-size:1.8em;margin:.3em 0 .5em} h2 {font-size:1.2em}
p {margin:0 0 1em} a {color:#12594d}
.kicker,.source {font-family:sans-serif;font-size:.8em}
.kicker {color:#9b4d28;letter-spacing:.06em}
.source {color:#52645c}
.why {border-left:.25em solid #5e897d;padding:.7em;background:#edf4ef;font-family:sans-serif;font-size:.9em}
.cover {margin-top:15%}.cover h1 {font-size:2.5em}
img {max-width:100%;height:auto} figure {margin:1em 0} li {margin-bottom:.6em}
.overview-group {border-top:2px solid #dce7e1;margin-top:1.6em;padding-top:.4em}
"""


def overview_group(article):
    value = (article.get("section", "") + " " + article.get("title", "")).lower()
    if any(word in value for word in ("danmark", "politik", "samfund", "økonomi", "kulturpolitik", "københavn")):
        return "Danmark og kultur"
    if any(word in value for word in ("ai", "teknologi", "apple", "mac", "verden", "global", "cyber")):
        return "Teknologi og verden"
    return "Fordybelse"


def sanitise_body(content, base_url):
    root = lxml_html.fragment_fromstring(content, create_parent="div")
    for node in root.xpath("//script|//style|//iframe|//form|//nav|//footer|//aside|//img|//picture|//svg|//video|//audio|//object|//pre|//code"):
        if node.getparent() is not None:
            node.drop_tree()
    allowed = {"div", "p", "h2", "h3", "h4", "blockquote", "ul", "ol", "li", "strong", "em", "b", "i", "a", "br", "span"}
    for node in list(root.iterdescendants()):
        if not isinstance(node.tag, str):
            continue
        href = canonical_url(urljoin(base_url, node.get("href", ""))) if node.tag == "a" and node.get("href") else ""
        node.attrib.clear()
        if node.tag not in allowed:
            node.drop_tag()
        elif href:
            node.set("href", href)
    return etree.tostring(root, encoding="unicode", method="xml")


def prepare_article(article, reader):
    try:
        raw, _, url = reader.get(article["url"])
        page = lxml_html.fromstring(raw)
        # Do not extract text that happens to be embedded behind a declared paywall.
        if re.search(rb'"isAccessibleForFree"\s*:\s*(?:false|"false")', raw, re.I):
            return None
        body = sanitise_body(Document(raw).summary(html_partial=True), url)
        text = lxml_html.fromstring(body).text_content()
        minimum_words = 550 if article.get("format") == "longread" else 260
        if len(text.split()) < minimum_words:
            logger.info("Skipping incomplete article from %s", article.get("source"))
            return None
        images = page.xpath("//meta[@property='og:image' or @name='og:image']/@content")
        languages = page.xpath("/html/@lang")
        return dict(article, url=url, body=body, reading_minutes=max(1, math.ceil(len(text.split()) / 220)),
                    language=languages[0] if languages else "und",
                    image_url=urljoin(url, images[0]) if images else "")
    except (requests.RequestException, ValueError, etree.Error) as exc:
        logger.info("Skipping unreadable article (%s)", type(exc).__name__)
        return None


def image_bytes(url, reader, config):
    raw, _, _ = reader.get(url, limit=int(config.get("max_bytes", 2500000)))
    with Image.open(io.BytesIO(raw)) as image:
        if image.width * image.height > 20_000_000:
            return None
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((int(config.get("max_width", 1200)), 1600))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=78, optimize=True)
        return output.getvalue()


def build_epub(articles, settings, output_dir=None, reader=None):
    output_dir = Path(output_dir or Path(__file__).parent / "output")
    output_dir.mkdir(parents=True, exist_ok=True)
    reader = reader or WebReader()
    today = datetime.now(ZoneInfo(settings["edition"]["timezone"])).date().isoformat()
    book = epub.EpubBook()
    title = f"{settings['edition']['title']} — {today}"
    book.set_identifier(f"mads-morgen-{today}")
    book.set_title(title)
    book.set_language("da")
    book.add_author("Mads Morgen")
    css = epub.EpubItem(uid="style", file_name="style.css", media_type="text/css", content=STYLE.encode())
    book.add_item(css)

    def chapter(title, filename, body, lang="da"):
        item = epub.EpubHtml(title=title, file_name=filename, lang=lang)
        item.content = body
        item.add_item(css)
        book.add_item(item)
        return item

    e = html.escape
    cover = chapter("Forside", "index.xhtml",
        f'<div class="cover"><p class="kicker">DIN PERSONLIGE MORGENAVIS</p><h1>{e(settings["edition"]["title"])}</h1>'
        f'<p>{today}</p><p>{len(articles)} historier · cirka {sum(a.get("reading_minutes", 1) for a in articles)} minutters læsning</p>'
        '<p>Det, der er værd at vide, før dagen begynder.</p></div>')
    groups = {"Danmark og kultur": [], "Teknologi og verden": [], "Fordybelse": []}
    for i, article in enumerate(articles, 1):
        groups[overview_group(article)].append((i, article))
    overview_body = '<h1>Dagens overblik</h1><p>De vigtigste historier først; læs resten, når du har tid.</p>'
    for heading, entries in groups.items():
        if entries:
            overview_body += f'<section class="overview-group"><h2>{e(heading)}</h2><ol>' + "".join(
                f'<li><a href="article-{i}.xhtml">{e(a["title"])}</a><p>{e(a.get("why", ""))}</p></li>'
                for i, a in entries) + "</ol></section>"
    overview = chapter("Dagens overblik", "overview.xhtml", overview_body)
    chapters = [cover, overview]
    image_settings, images_added = settings.get("images", {}), 0
    for i, article in enumerate(articles, 1):
        picture = ""
        if (image_settings.get("enabled") and article.get("use_image") and article.get("image_url")
                and images_added < min(4, int(image_settings.get("max_per_edition", 4)))):
            try:
                raw = image_bytes(article["image_url"], reader, image_settings)
                if raw:
                    filename = f"images/article-{i}.jpg"
                    book.add_item(epub.EpubItem(uid=f"image-{i}", file_name=filename, media_type="image/jpeg", content=raw))
                    picture = f'<figure><img src="{filename}" alt="Billede fra artiklen"/></figure>'
                    images_added += 1
            except Exception as exc:
                logger.info("Image omitted (%s)", type(exc).__name__)
        body = article.get("body", "")
        if not body:
            raise ValueError("Only prepared, readable articles may enter an EPUB")
        content = (f'<p class="kicker">{e(article.get("section", "Udvalgt"))}</p><h1>{e(article["title"])}</h1>'
                   f'<p class="source">{e(article["source"])} · {article.get("reading_minutes", 1)} min.</p>{picture}'
                   f'<div class="why"><strong>Kort fortalt:</strong> {e(article.get("why", ""))}</div>'
                   f'<div lang="{e(article.get("language", "und"))}">{body}</div>'
                   f'<p><a href="{e(article["url"], quote=True)}">Læs originalen</a></p>')
        chapters.append(chapter(article["title"], f"article-{i}.xhtml", content))
    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    nav = epub.EpubNav()
    nav.add_item(css)
    book.add_item(nav)
    book.spine = [*chapters]
    path = output_dir / f"mads-morgen-{today}.epub"
    epub.write_epub(str(path), book)
    return path
