"""Offline, reflowable EPUB in the approved Mads Morgen layout."""
import html
import io
import logging
import math
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from ebooklib import epub
from lxml import etree, html as lxml_html
from PIL import Image, ImageDraw, ImageFont, ImageOps
from readability import Document
import requests

from news_io import BudgetExhausted, WebReader, canonical_url

logger = logging.getLogger(__name__)
STYLE = """
body {font-family:serif;line-height:1.5;max-width:34em;margin:0 auto;padding:1.5em 1.2em;color:#18201c}
h1,h2,h3 {font-family:sans-serif;line-height:1.2;color:#123f38}
h1 {font-size:1.8em;margin:.3em 0 .5em} h2 {font-size:1.2em}
p {margin:0 0 1em} a {color:#12594d}
.kicker,.source {font-family:sans-serif;font-size:.8em}
.kicker {color:#9b4d28;letter-spacing:.06em}
.source {color:#52645c}
.why {border-left:.25em solid #5e897d;padding:.7em;background:#edf4ef;font-family:sans-serif;font-size:.9em}
.cover {margin-top:15%}.cover h1 {font-size:2.5em}
img {max-width:100%;height:auto} figure {margin:1em 0} li {margin-bottom:.6em}
.overview-group {border-top:2px solid #dce7e1;margin-top:1.6em;padding-top:.4em}
.overview-group h3 {font-size:1em;color:#9b4d28;letter-spacing:.06em;text-transform:uppercase}
.rest {list-style:none;padding-left:0}
.rest li {margin-bottom:.9em}
.meta {font-family:sans-serif;font-size:.78em;color:#52645c;display:block;margin-top:.15em}
"""


GROUPS = ("Danmark og kultur", "Teknologi og verden", "Fordybelse")
# Whole-word matching only. Substring matching used to file "Ukraine",
# "detailhandel" and "Thailand" under Teknologi, because all three contain "ai".
GROUP_WORDS = {
    "Danmark og kultur": {"danmark", "dansk", "danske", "politik", "politisk", "samfund", "økonomi",
                          "kultur", "kulturpolitik", "københavn", "folketinget", "regeringen",
                          "kommune", "museum", "museer", "film", "biograf"},
    "Teknologi og verden": {"ai", "teknologi", "tech", "apple", "mac", "iphone", "verden", "global",
                            "globalt", "cyber", "sikkerhed", "udland", "eu", "usa", "software",
                            "chip", "robot", "data"},
}


def overview_group(article):
    """Use the group the editor already chose; fall back to whole-word matching."""
    declared = str(article.get("group", "")).strip()
    if declared in GROUPS:
        return declared
    words = set(re.findall(r"[\wæøåÆØÅ]+", (article.get("section", "") + " " + article.get("title", "")).lower()))
    for group in ("Danmark og kultur", "Teknologi og verden"):
        if words & GROUP_WORDS[group]:
            return group
    return "Fordybelse"


def sanitise_body(content, base_url):
    root = lxml_html.fragment_fromstring(content, create_parent="div")
    # Reading-view HTML is often still littered with the publisher's media
    # widgets.  They add captions twice (TV 2's "Åbn Billedefremviser" is a
    # typical example) but no useful article prose: we use the separately
    # fetched Open Graph image when an image belongs in the EPUB.
    for node in root.xpath("//script|//style|//iframe|//form|//nav|//footer|//aside|//figure|//figcaption|//img|//picture|//svg|//video|//audio|//object|//pre|//code"):
        if node.getparent() is not None:
            node.drop_tree()
    # Subscription prompts, events and newsletter CTAs are not part of the
    # story.  This is deliberately local and rules-based: it costs no tokens
    # and works even when a source changes its page template.
    promotional_markers = (
        "subscriber-exclusive", "subscribe to", "subscribe now", "sign up for",
        "sign up to", "join me and my colleagues", "roundtable discussion",
        "newsletter", "email preferences", "become a member", "bliv abonnent",
        "tilmeld dig", "abonnér på",
    )
    for node in reversed(list(root.iterdescendants())):
        if not isinstance(node.tag, str) or node.getparent() is None:
            continue
        node_text = " ".join(node.text_content().split()).lower()
        if node_text.startswith("åbn billedefremviser") or any(marker in node_text for marker in promotional_markers):
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


def unsuitable_article_text(text):
    """Reject pages which are clearly not a complete editorial article.

    An extractor cannot responsibly invent a missing opening.  Returning a
    reserve article is better than sending a Kobo page that begins in the
    middle of a political argument or is really a subscriber event landing
    page.
    """
    compact = " ".join(str(text or "").split()).lower()
    opening = compact[:260]
    continuation_openings = (
        "og således tilbage til spørgsmålet",
        "tilbage til spørgsmålet om",
        "som tidligere nævnt",
        "fortsættelse følger",
    )
    landing_page_markers = (
        "frequently asked questions",
        "what is roundtables",
        "the series is only available to",
    )
    return (not compact or any(marker in opening for marker in continuation_openings)
            or any(marker in compact for marker in landing_page_markers))


def prepare_article(article, reader):
    try:
        raw, _, url = reader.get(article["url"])
        page = lxml_html.fromstring(raw)
        # Do not extract text that happens to be embedded behind a declared paywall.
        if re.search(rb'"isAccessibleForFree"\s*:\s*(?:false|"false")', raw, re.I):
            return None
        body = sanitise_body(Document(raw).summary(html_partial=True), url)
        text = lxml_html.fromstring(body).text_content()
        if unsuitable_article_text(text):
            logger.info("Skipping incomplete or promotional page from %s", article.get("source"))
            return None
        word_count = len(text.split())
        minimum_words = 550 if article.get("format") == "longread" else 260
        if word_count < minimum_words:
            logger.info("Skipping incomplete article from %s", article.get("source"))
            return None
        images = page.xpath("//meta[@property='og:image' or @name='og:image']/@content")
        languages = page.xpath("/html/@lang")
        return dict(article, url=url, body=body, word_count=word_count,
                    reading_minutes=max(1, math.ceil(word_count / 220)),
                    language=languages[0] if languages else "und",
                    image_url=urljoin(url, images[0]) if images else "")
    except BudgetExhausted:
        raise
    except (requests.RequestException, ValueError, etree.Error) as exc:
        logger.info("Skipping unreadable article (%s)", type(exc).__name__)
        return None


FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)
MONTHS = ("januar", "februar", "marts", "april", "maj", "juni",
          "juli", "august", "september", "oktober", "november", "december")
WEEKDAYS = ("mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag")


def danish_date(day):
    return f"{WEEKDAYS[day.weekday()]} {day.day}. {MONTHS[day.month - 1]} {day.year}"


def short_date(value):
    """Return a compact Danish date for ISO-8601 or RSS/RFC 2822 input."""
    text = str(value or "").strip()
    if not text:
        return ""
    for parse in (lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")),
                  parsedate_to_datetime):
        try:
            stamp = parse(text)
        except (TypeError, ValueError, OverflowError):
            continue
        return f"{stamp.day}. {MONTHS[stamp.month - 1][:3]}"
    return ""


def reading_span(minutes):
    """Use hours for long editions while keeping short editions natural."""
    minutes = max(1, int(minutes))
    if minutes < 90:
        return f"{minutes} minutters læsning"
    return f"{minutes // 60} t {minutes % 60} min. læsning"


def article_meta(article):
    """The compact information needed to decide when to read an article."""
    return " · ".join(part for part in (
        str(article.get("source", "")), short_date(article.get("published")),
        f'{article.get("reading_minutes", 1)} min.') if part)


def build_overview(articles):
    """Keep the overview short; detailed reasoning belongs only on the top five."""
    e = html.escape
    essentials = list(enumerate(articles[:5], 1))
    groups = {group: [] for group in GROUPS}
    for number, article in enumerate(articles[5:], 6):
        groups[overview_group(article)].append((number, article))
    body = '<h1>Dagens overblik</h1><p>Redaktørens prioritering af det vigtigste og det mest interessante i dag.</p>'
    if essentials:
        body += '<section class="overview-group"><h2>Hvis du kun læser fem</h2><ol>' + "".join(
            f'<li><a href="article-{number}.xhtml">{e(article["title"])}</a>'
            f'<span class="meta">{e(article_meta(article))}</span><p>{e(article.get("why", ""))}</p></li>'
            for number, article in essentials) + "</ol></section>"
    if any(groups.values()):
        body += '<h2>Resten af avisen</h2>'
    for heading, entries in groups.items():
        if entries:
            body += f'<section class="overview-group"><h3>{e(heading)}</h3><ul class="rest">' + "".join(
                f'<li><a href="article-{number}.xhtml">{e(article["title"])}</a>'
                f'<span class="meta">{e(article_meta(article))}</span></li>'
                for number, article in entries) + "</ul></section>"
    return body


def load_font(size):
    """A real font when the host has one, otherwise Pillow's bundled scalable font.

    Render's image does not always ship system fonts, and load_default() without a
    size is an unreadable bitmap, so never rely on the system font being there.
    """
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def wrap_text(draw, text, font, width):
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and draw.textlength(candidate, font=font) > width:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def build_cover_image(articles, settings, day, hero=None):
    """A dated front page so ten editions are not ten identical books on the Kobo.

    Drawn locally with Pillow: no API call, no per-day cost, works offline.
    """
    width, height, margin = 1200, 1600, 80
    paper, ink, accent, muted = (244, 241, 232), (18, 40, 34), (155, 77, 40), (82, 100, 92)
    canvas = Image.new("RGB", (width, height), paper)
    draw = ImageDraw.Draw(canvas)
    inner = width - 2 * margin
    y = margin

    draw.text((margin, y), "DIN PERSONLIGE MORGENAVIS", font=load_font(26), fill=accent)
    y += 46
    draw.text((margin, y), settings["edition"]["title"], font=load_font(104), fill=ink)
    y += 128
    draw.text((margin, y), danish_date(day), font=load_font(34), fill=muted)
    y += 52
    minutes = sum(article.get("reading_minutes", 1) for article in articles)
    draw.text((margin, y), f"{len(articles)} historier · cirka {reading_span(minutes)}", font=load_font(30), fill=muted)
    y += 58
    draw.line((margin, y, width - margin, y), fill=ink, width=3)
    y += 34

    # The library cover is a glanceable front page, not a second contents page.
    # Omitting the hero leaves enough room for all five priorities at large type.
    heading_font, title_font, meta_font = load_font(28), load_font(38), load_font(26)
    draw.text((margin, y), "HVIS DU KUN LÆSER FEM", font=heading_font, fill=accent)
    y += 52
    for article in articles[:5]:
        if y > height - 150:
            break
        for line in wrap_text(draw, article.get("title", ""), title_font, inner)[:2]:
            draw.text((margin, y), line, font=title_font, fill=ink)
            y += 48
        draw.text((margin, y), f'{article.get("source", "")} · {article.get("reading_minutes", 1)} min.',
                  font=meta_font, fill=muted)
        y += 52

    output = io.BytesIO()
    canvas.save(output, format="JPEG", quality=82, optimize=True)
    return output.getvalue()


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
    day = datetime.now(ZoneInfo(settings["edition"]["timezone"])).date()
    today = day.isoformat()
    book = epub.EpubBook()
    title = f"{settings['edition']['title']} — {today}"
    book.set_identifier(f"mads-morgen-{today}")
    book.set_title(title)
    book.set_language("da")
    book.add_author("Mads Morgen")
    css = epub.EpubItem(uid="style", file_name="style.css", media_type="text/css", content=STYLE.encode())
    book.add_item(css)

    image_settings, images_added = settings.get("images", {}), 0
    cover_name = ""
    try:
        cover_name = "cover.jpg"
        book.set_cover(cover_name, build_cover_image(articles, settings, day), create_page=False)
    except Exception as exc:  # noqa: BLE001
        cover_name = ""
        logger.warning("Cover could not be generated (%s)", type(exc).__name__)

    def chapter(title, filename, body, lang="da"):
        item = epub.EpubHtml(title=title, file_name=filename, lang=lang)
        item.content = body
        item.add_item(css)
        book.add_item(item)
        return item

    e = html.escape
    cover_picture = ""
    total_reading = reading_span(sum(a.get("reading_minutes", 1) for a in articles))
    cover = chapter("Forside", "index.xhtml",
        f'{cover_picture}<div class="cover"><p class="kicker">DIN PERSONLIGE MORGENAVIS</p><h1>{e(settings["edition"]["title"])}</h1>'
        f'<p>{e(danish_date(day))}</p><p>{len(articles)} historier · cirka {e(total_reading)}</p>'
        '<p>Det, der er værd at vide, før dagen begynder.</p></div>')
    overview = chapter("Dagens overblik", "overview.xhtml", build_overview(articles))
    chapters = [cover, overview]
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
        source_line = e(article_meta(article))
        content = (f'<p class="kicker">{e(article.get("section", "Udvalgt"))}</p><h1>{e(article["title"])}</h1>'
                   f'<p class="source">{source_line}</p>{picture}'
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


def build_notice_epub(settings, reason, output_dir=None):
    """A one-page edition saying why today's paper is missing.

    The Kobo is where the paper is read, so it is also where a failure has to be
    visible. Its own '-status' filename, never the real edition's: a failing
    manual run at midday must not overwrite the paper that went out at 05:30.
    Drive retention still recognises and prunes it.
    """
    output_dir = Path(output_dir or Path(__file__).parent / "output")
    output_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(ZoneInfo(settings["edition"]["timezone"])).date()
    today = day.isoformat()
    book = epub.EpubBook()
    book.set_identifier(f"mads-morgen-{today}-status")
    book.set_title(f"{settings['edition']['title']} — {today} (kunne ikke udkomme)")
    book.set_language("da")
    book.add_author("Mads Morgen")
    css = epub.EpubItem(uid="style", file_name="style.css", media_type="text/css", content=STYLE.encode())
    book.add_item(css)
    e = html.escape
    page = epub.EpubHtml(title="Ingen avis i dag", file_name="index.xhtml", lang="da")
    page.content = (f'<div class="cover"><p class="kicker">INGEN AVIS I DAG</p>'
                    f'<h1>{e(settings["edition"]["title"])}</h1><p>{e(danish_date(day))}</p></div>'
                    f'<div class="why"><strong>Hvad gik galt:</strong> {e(str(reason)[:600])}</div>'
                    '<p>Kørslen prøver igen i morgen. Du kan starte en ny kørsel fra kontrolpanelet.</p>')
    page.add_item(css)
    book.add_item(page)
    book.toc = (page,)
    book.add_item(epub.EpubNcx())
    nav = epub.EpubNav()
    nav.add_item(css)
    book.add_item(nav)
    book.spine = [page]
    path = output_dir / f"mads-morgen-{today}-status.epub"
    epub.write_epub(str(path), book)
    return path
