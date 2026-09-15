"""Low-cost archiver for public magazine PDFs chosen by the user.

This deliberately has no AI calls.  It only reads each configured archive page
and follows links that are already direct public PDF files.  Reader embeds,
paywalls and login pages are reported rather than worked around.
"""
import logging
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import requests
from googleapiclient.http import MediaFileUpload
from lxml import html as lxml_html

from drive_delivery import drive_service, list_files, quote
from news_io import canonical_url, public_url

logger = logging.getLogger(__name__)
PDF_MIME = "application/pdf"
GENERATOR = "mads-morgen-magazine"
MONTH_SLUGS_DA = ("januar", "februar", "marts", "april", "maj", "juni",
                  "juli", "august", "september", "oktober", "november", "december")


def slug(value):
    """A stable Drive-safe source key; the visible title remains unchanged."""
    text = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return text[:70] or "magasin"


def public_pdf_links(archive_url, raw, keep, url_pattern=None):
    """Return approved direct PDF links in the archive's own order.

    ``url_pattern`` is intentionally source-specific.  A publisher's general
    website can contain unrelated PDFs (job adverts, press kits or forms),
    which must never be mistaken for a magazine issue.
    """
    page = lxml_html.fromstring(raw)
    bases = page.xpath("//base[@href]/@href")
    link_base = urljoin(archive_url, bases[0]) if bases else archive_url
    allowed = re.compile(url_pattern, re.I) if url_pattern else None
    seen, issues = set(), []
    for link in page.xpath("//a[@href]"):
        href = canonical_url(urljoin(link_base, link.get("href", "")))
        if not href or href in seen:
            continue
        if allowed and not allowed.search(href):
            continue
        path = urlsplit(href).path.lower()
        label = " ".join(link.text_content().split())
        # Several journal platforms use a download endpoint without a .pdf
        # suffix. It is still safe: download_public_pdf verifies the PDF magic
        # bytes before anything is saved.
        if not path.endswith(".pdf") and not re.search(r"\bdownload\b.{0,24}\bpdf\b", label, re.I):
            continue
        seen.add(href)
        title = label
        # An image-only link often has the useful issue label one level up.
        if not title and link.getparent() is not None:
            title = " ".join(link.getparent().text_content().split())
        issues.append({"url": href, "title": title[:180] or Path(unquote(path)).stem})
        if len(issues) >= keep:
            break
    return issues


def issue_page_links(archive_url, raw, pattern, keep):
    """Find the newest issue pages when their PDFs live one official click in."""
    page = lxml_html.fromstring(raw)
    bases = page.xpath("//base[@href]/@href")
    link_base = urljoin(archive_url, bases[0]) if bases else archive_url
    seen, issues = set(), []
    expression = re.compile(pattern, re.I)
    for link in page.xpath("//a[@href]"):
        href = canonical_url(urljoin(link_base, link.get("href", "")))
        if not href or href in seen or not expression.search(href):
            continue
        seen.add(href)
        title = " ".join(link.text_content().split())
        issues.append({"url": href, "title": title[:180] or Path(urlsplit(href).path).name})
        if len(issues) >= keep:
            break
    return issues


def sequential_issue_pages(issues, keep):
    """Backfill numeric issue URLs when an archive only renders the newest one."""
    if not issues:
        return issues
    match = re.match(r"^(.*?/)([0-9]+)$", issues[0]["url"])
    if not match:
        return issues
    prefix, newest = match.groups()
    seen = {issue["url"] for issue in issues}
    for number in range(int(newest) - 1, max(-1, int(newest) - keep), -1):
        url = f"{prefix}{number}"
        if url not in seen:
            issues.append({"url": url, "title": str(number)})
            seen.add(url)
    return issues


def discover_issues(source, archive_url, raw, keep):
    """Use direct archive PDFs first, then follow only configured official issue pages."""
    allowed_urls = source.get("pdf_url_pattern")
    direct = public_pdf_links(archive_url, raw, keep, allowed_urls)
    if direct:
        return direct
    pattern = source.get("issue_url_pattern")
    if not pattern:
        return []
    page_limit = max(keep, int(source.get("max_issue_pages", keep)))
    issue_pages = issue_page_links(archive_url, raw, pattern, page_limit)
    if source.get("sequential_issue_pages"):
        issue_pages = sequential_issue_pages(issue_pages, page_limit)
    resolved = []
    for issue in issue_pages:
        response = requests.get(public_url(issue["url"]), timeout=(5, 25),
                                headers={"User-Agent": "MadsMorgen/1.0"})
        response.raise_for_status()
        files = public_pdf_links(response.url, response.content, 1, allowed_urls)
        # A few open journal platforms expose an issue landing page first and
        # place its one official PDF link on a second, dedicated download page.
        # This remains deliberately bounded: one configured extra link only.
        if not files and source.get("download_page_pattern"):
            nested = issue_page_links(response.url, response.content,
                                      source["download_page_pattern"], 1)
            if nested:
                download_page = requests.get(public_url(nested[0]["url"]), timeout=(5, 25),
                                             headers={"User-Agent": "MadsMorgen/1.0"})
                download_page.raise_for_status()
                files = public_pdf_links(download_page.url, download_page.content, 1, allowed_urls)
        if files:
            files[0]["title"] = issue["title"]
            resolved.append(files[0])
            if len(resolved) >= keep:
                break
    return resolved


def configured_issue_pages(source, keep, today=None):
    """Use an explicitly configured official reader page, newest first.

    Some publishers' archives lag behind their reader.  A configured page lets
    us use the publisher's own download action without scanning an old archive.
    The stable reader URL is kept as the issue origin; the short-lived download
    URL is never used for deduplication.
    """
    today = today or date.today()
    urls = []
    template = source.get("current_issue_url_template")
    if template:
        urls.append(template.format(year=today.year, month=MONTH_SLUGS_DA[today.month - 1]))
    urls.extend(source.get("issue_urls", []))
    issues, seen = [], set()
    for url in urls:
        url = canonical_url(url)
        if url and url not in seen:
            issues.append({"url": url, "title": Path(urlsplit(url).path).name})
            seen.add(url)
        if len(issues) >= keep:
            break
    return issues


def reader_download_issues(source, keep):
    """Build the official reader download action for configured issue pages."""
    issues = configured_issue_pages(source, keep)
    action = source.get("download_post_path")
    if not action:
        return issues
    for issue in issues:
        issue["download_post_url"] = canonical_url(urljoin(issue["url"].rstrip("/") + "/", action))
    return issues


def stale_files(existing, desired, uploads, keep, retain_existing):
    """Return only app-owned files that should be removed from this title."""
    if not retain_existing:
        return [file for file in existing
                if file.get("appProperties", {}).get("origin") not in desired]
    excess = max(0, len(existing) + uploads - keep)
    return sorted(existing, key=lambda file: file.get("createdTime", ""))[:excess]


def download_public_pdf(url, maximum_bytes):
    """Download only a real PDF from a public endpoint, with a hard size cap."""
    current = canonical_url(url)
    for _ in range(4):
        current = public_url(current)
        with requests.get(current, timeout=(5, 35), stream=True, allow_redirects=False,
                          headers={"User-Agent": "MadsMorgen/1.0 (personal magazine archive)"}) as response:
            if response.is_redirect:
                current = urljoin(current, response.headers.get("Location", ""))
                continue
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > maximum_bytes:
                    raise ValueError("PDF exceeds configured size limit")
            if not bytes(content).startswith(b"%PDF-"):
                raise ValueError("Archive link was not a PDF")
            return bytes(content), current
    raise ValueError("Too many redirects")


def download_reader_pdf(post_url, maximum_bytes):
    """Use a publisher's own public reader download action, then fetch its PDF."""
    response = requests.post(public_url(post_url), data={"pageNumbers": ""},
                             timeout=(5, 25), allow_redirects=False,
                             headers={"User-Agent": "MadsMorgen/1.0 (personal magazine archive)"})
    if not response.is_redirect:
        response.raise_for_status()
        raise ValueError("Reader did not provide a PDF download redirect")
    target = response.headers.get("Location", "")
    if not target:
        raise ValueError("Reader download redirect had no target")
    return download_public_pdf(urljoin(post_url, target), maximum_bytes)


def managed_files(drive, folder_id, source_key):
    query = (f"'{quote(folder_id)}' in parents and trashed=false and "
             f"appProperties has {{ key='generator' and value='{GENERATOR}' }} and "
             f"appProperties has {{ key='source' and value='{quote(source_key)}' }}")
    return list_files(drive, query, "id,name,mimeType,createdTime,appProperties")


def store_issue(drive, folder_id, source, issue, content, final_url):
    source_key = slug(source["name"])
    basename = Path(unquote(urlsplit(final_url).path)).name or "issue.pdf"
    filename = f"magasin--{source_key}--{basename}"[:220]
    with tempfile.NamedTemporaryFile(suffix=".pdf") as temporary:
        temporary.write(content)
        temporary.flush()
        metadata = {
            "name": filename,
            "mimeType": PDF_MIME,
            "parents": [folder_id],
            "appProperties": {"generator": GENERATOR, "source": source_key,
                              "origin": canonical_url(issue["url"]), "title": issue["title"]},
        }
        return drive.files().create(body=metadata,
            media_body=MediaFileUpload(temporary.name, mimetype=PDF_MIME, resumable=True),
            fields="id,name").execute()


def sync_magazines(config, drive=None):
    """Check archive pages and retain exactly the newest public PDFs per title.

    A source without direct PDFs is not an error: it remains visible in the
    report as ``no_public_pdf``.  This protects publishers' reader/paywall
    choices while still making the scanner useful for every source that exposes
    a normal public file.
    """
    if not config.get("enabled", False):
        return {"enabled": False, "sources": []}
    folder_id = os.environ.get("GOOGLE_MAGAZINES_FOLDER_ID") or os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
    if not folder_id:
        raise RuntimeError("GOOGLE_DRIVE_FOLDER_ID mangler")
    drive = drive or drive_service()
    keep = max(1, int(config.get("keep_per_title", 6)))
    default_maximum_bytes = max(1_000_000, int(config.get("max_pdf_bytes", 120_000_000)))
    report = {"enabled": True, "sources": [], "uploaded": 0}
    for source in config.get("sources", []):
        if not source.get("enabled", True):
            continue
        name, source_key = source["name"], slug(source["name"])
        item = {"name": name, "status": "ok", "found": 0, "uploaded": 0}
        try:
            maximum_bytes = max(1_000_000, int(source.get("max_pdf_bytes", default_maximum_bytes)))
            if source.get("issue_urls"):
                issues = reader_download_issues(source, keep)
            else:
                archive = public_url(source["archive_url"])
                response = requests.get(archive, timeout=(5, 25), headers={"User-Agent": "MadsMorgen/1.0"})
                response.raise_for_status()
                issues = discover_issues(source, response.url, response.content, keep)
            item["found"] = len(issues)
            if not issues:
                item["status"] = "no_public_pdf"
                report["sources"].append(item)
                continue
            existing = managed_files(drive, folder_id, source_key)
            origin_pattern = source.get("origin_url_pattern")
            if origin_pattern:
                approved = re.compile(origin_pattern, re.I)
                invalid = [file for file in existing
                           if not approved.search(file.get("appProperties", {}).get("origin", ""))]
                for file in invalid:
                    drive.files().update(fileId=file["id"], body={"trashed": True}).execute()
                existing = [file for file in existing if file not in invalid]
            by_origin = {file.get("appProperties", {}).get("origin"): file for file in existing}
            desired = {canonical_url(issue["url"]) for issue in issues}
            for issue in issues:
                if canonical_url(issue["url"]) in by_origin:
                    continue
                if issue.get("download_post_url"):
                    content, final_url = download_reader_pdf(issue["download_post_url"], maximum_bytes)
                else:
                    content, final_url = download_public_pdf(issue["url"], maximum_bytes)
                store_issue(drive, folder_id, source, issue, content, final_url)
                item["uploaded"] += 1
                report["uploaded"] += 1
            # Never touch unrelated Drive files. A progressive collection keeps
            # prior app-owned issues and only removes its oldest item at the cap.
            for file in stale_files(existing, desired, item["uploaded"], keep,
                                    source.get("retain_existing", False)):
                drive.files().update(fileId=file["id"], body={"trashed": True}).execute()
            item["kept"] = len(issues)
        except (requests.RequestException, ValueError, OSError) as exc:
            item["status"] = "error"
            item["error"] = type(exc).__name__
            logger.warning("Magazine scan failed for %s (%s)", name, type(exc).__name__)
        report["sources"].append(item)
    return report
