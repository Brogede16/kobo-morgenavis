import magazine_downloader as magazines
from datetime import date


def test_public_pdf_links_keeps_direct_files_in_archive_order():
    raw = '''<html><body>
      <a href="/new.pdf">Nyeste nummer</a>
      <a href="https://reader.example.test/issue">Indlejret læser</a>
      <a href="/older.pdf?utm_source=x">Forrige nummer</a>
      <a href="/third.pdf">Tredje</a>
    </body></html>'''.encode()
    issues = magazines.public_pdf_links("https://archive.example.test/issues", raw, 2)
    assert [issue["title"] for issue in issues] == ["Nyeste nummer", "Forrige nummer"]
    assert issues[1]["url"] == "https://archive.example.test/older.pdf"


def test_public_pdf_links_accepts_a_public_download_endpoint_without_pdf_suffix():
    raw = b'<html><a href="/download/issue-65">Download PDF</a></html>'
    issues = magazines.public_pdf_links("https://archive.example.test/issues", raw, 1)
    assert issues == [{"url": "https://archive.example.test/download/issue-65", "title": "Download PDF"}]


def test_public_pdf_links_respects_the_source_specific_allowlist():
    raw = b'''<html>
      <a href="/jobs/branding.pdf">Job advert</a>
      <a href="/issues/autumn.pdf">Magazine</a>
    </html>'''
    issues = magazines.public_pdf_links(
        "https://publisher.example.test", raw, 6,
        r"^https://publisher\.example\.test/issues/.+\.pdf$",
    )
    assert issues == [{"url": "https://publisher.example.test/issues/autumn.pdf", "title": "Magazine"}]


def test_discover_issues_follows_the_official_issue_page(monkeypatch):
    class Response:
        def __init__(self, url, content):
            self.url, self.content = url, content

        def raise_for_status(self):
            pass

    responses = iter([Response(
        "https://archive.example.test/issues/2026-09",
        b'<html><a href="/download/2026-09">Download PDF</a></html>',
    )])
    monkeypatch.setattr(magazines, "public_url", lambda url: url)
    monkeypatch.setattr(magazines.requests, "get", lambda *args, **kwargs: next(responses))
    archive = b'<html><a href="/issues/2026-09">September 2026</a></html>'
    issues = magazines.discover_issues(
        {"issue_url_pattern": r"/issues/"}, "https://archive.example.test", archive, 6,
    )
    assert issues == [{
        "url": "https://archive.example.test/download/2026-09",
        "title": "September 2026",
    }]


def test_discover_issues_honours_a_page_base_and_one_official_download_page(monkeypatch):
    class Response:
        def __init__(self, url, content):
            self.url, self.content = url, content

        def raise_for_status(self):
            pass

    responses = iter([
        Response("https://archive.example.test/issues/42", b'''<html>
          <a href="/issues/42/download">Get the issue</a></html>'''),
        Response("https://archive.example.test/issues/42/download", b'''<html>
          <base href="https://archive.example.test/">
          <a href="files/issue-42.pdf">Download PDF</a></html>'''),
    ])
    monkeypatch.setattr(magazines, "public_url", lambda url: url)
    monkeypatch.setattr(magazines.requests, "get", lambda *args, **kwargs: next(responses))
    archive = b'<html><a href="/issues/42">Issue 42</a></html>'
    issues = magazines.discover_issues({
        "issue_url_pattern": r"/issues/[0-9]+$",
        "download_page_pattern": r"/issues/[0-9]+/download$",
    }, "https://archive.example.test", archive, 6)
    assert issues == [{
        "url": "https://archive.example.test/files/issue-42.pdf",
        "title": "Issue 42",
    }]


def test_sequential_issue_pages_backfills_from_the_newest_public_issue():
    pages = magazines.sequential_issue_pages([
        {"url": "https://journal.example.test/issues/165", "title": "165"},
    ], 4)
    assert [page["url"] for page in pages] == [
        "https://journal.example.test/issues/165",
        "https://journal.example.test/issues/164",
        "https://journal.example.test/issues/163",
        "https://journal.example.test/issues/162",
    ]


def test_reader_download_issues_keeps_stable_issue_url_and_uses_official_action():
    issues = magazines.reader_download_issues({
        "issue_urls": ["https://reader.example.test/2026/september/?page=1"],
        "download_post_path": "GetPDF.ashx",
    }, 6)
    assert issues == [{
        "url": "https://reader.example.test/2026/september/?page=1",
        "title": "september",
        "download_post_url": "https://reader.example.test/2026/september/GetPDF.ashx",
    }]


def test_download_reader_pdf_uses_the_public_redirect(monkeypatch):
    class Response:
        is_redirect = True
        headers = {"Location": "https://cdn.example.test/fresh-signed.pdf"}

        def raise_for_status(self):
            raise AssertionError("a redirect is expected")

    monkeypatch.setattr(magazines, "public_url", lambda url: url)
    monkeypatch.setattr(magazines.requests, "post", lambda *args, **kwargs: Response())
    monkeypatch.setattr(magazines, "download_public_pdf",
                        lambda url, maximum: (b"%PDF-test", url))
    content, url = magazines.download_reader_pdf("https://reader.example.test/GetPDF.ashx", 123)
    assert content == b"%PDF-test"
    assert url == "https://cdn.example.test/fresh-signed.pdf"


def test_configured_issue_pages_checks_current_month_before_its_safe_fallback():
    issues = magazines.configured_issue_pages({
        "current_issue_url_template": "https://reader.example.test/{year}/{month}/?page=1",
        "issue_urls": ["https://reader.example.test/2026/september/?page=1"],
    }, 6, today=date(2026, 10, 1))
    assert [issue["url"] for issue in issues] == [
        "https://reader.example.test/2026/oktober/?page=1",
        "https://reader.example.test/2026/september/?page=1",
    ]


def test_stale_files_keeps_a_progressive_collection_until_it_reaches_its_cap():
    existing = [
        {"id": "old", "createdTime": "2026-01-01T00:00:00Z", "appProperties": {"origin": "old"}},
        {"id": "recent", "createdTime": "2026-02-01T00:00:00Z", "appProperties": {"origin": "recent"}},
    ]
    assert magazines.stale_files(existing, {"new"}, 1, 3, True) == []
    assert magazines.stale_files(existing, {"new"}, 2, 3, True) == [existing[0]]


def test_sync_reports_reader_only_archives_without_downloading(monkeypatch):
    class Response:
        url = "https://archive.example.test/issues"
        content = '<html><a href="https://reader.example.test/42">Læs online</a></html>'.encode()
        def raise_for_status(self): pass

    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "folder")
    monkeypatch.setattr(magazines, "public_url", lambda url: url)
    monkeypatch.setattr(magazines.requests, "get", lambda *args, **kwargs: Response())
    report = magazines.sync_magazines({"enabled": True, "sources": [
        {"name": "Kun læser", "archive_url": "https://archive.example.test/issues"}
    ]}, drive=object())
    assert report["uploaded"] == 0
    assert report["sources"] == [{"name": "Kun læser", "status": "no_public_pdf", "found": 0, "uploaded": 0}]


def test_sync_keeps_only_the_current_archive_urls(monkeypatch):
    class Response:
        url = "https://archive.example.test/issues"
        content = b'<html><a href="/new.pdf">Ny</a><a href="/old.pdf">Gammel</a></html>'
        def raise_for_status(self): pass

    class Files:
        def __init__(self):
            self.trashed = []
        def list(self, **kwargs):
            return type("Request", (), {"execute": lambda _: {"files": [{
                "id": "expired", "appProperties": {"origin": "https://archive.example.test/expired.pdf"}
            }]}})()
        def update(self, fileId, body):
            self.trashed.append((fileId, body))
            return type("Request", (), {"execute": lambda _: {}})()

    class Drive:
        def __init__(self): self.api = Files()
        def files(self): return self.api

    drive = Drive()
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "folder")
    monkeypatch.setattr(magazines, "public_url", lambda url: url)
    monkeypatch.setattr(magazines.requests, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(magazines, "download_public_pdf", lambda url, maximum: (b"%PDF-test", url))
    monkeypatch.setattr(magazines, "store_issue", lambda *args: {"id": "new"})
    report = magazines.sync_magazines({"enabled": True, "keep_per_title": 2, "sources": [
        {"name": "Arkiv", "archive_url": "https://archive.example.test/issues"}
    ]}, drive=drive)
    assert report["uploaded"] == 2
    assert drive.api.trashed == [("expired", {"trashed": True})]
