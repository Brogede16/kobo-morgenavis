import magazine_downloader as magazines


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
