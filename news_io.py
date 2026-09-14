"""Bounded public-web retrieval shared by feeds, previews and EPUB extraction."""
import ipaddress
import socket
import time
from collections import OrderedDict
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests


class BudgetExhausted(RuntimeError):
    """The edition-wide network safety budget has been used up."""


def canonical_url(url):
    try:
        parts = urlsplit(str(url))
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            return ""
        if parts.port not in {None, 80, 443}:
            return ""
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", urlencode(query), ""))
    except ValueError:
        return ""


def public_url(url):
    url = canonical_url(url)
    if not url:
        raise ValueError("Invalid public URL")
    host = urlsplit(url).hostname
    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Could not resolve public host") from exc
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError("Non-public destination")
    return url


class WebReader:
    """One edition's cache, request count and actual downloaded-byte budget."""

    def __init__(self, max_requests=180, max_bytes=35_000_000, max_seconds=1800,
                 max_cache_bytes=25_000_000, max_cache_item_bytes=1_000_000):
        self.max_requests, self.max_bytes = max_requests, max_bytes
        self.deadline = time.monotonic() + max_seconds
        self.max_cache_bytes, self.max_cache_item_bytes = max_cache_bytes, max_cache_item_bytes
        self.requests = self.bytes = 0
        self.cache_bytes = 0
        self.cache = OrderedDict()

    def check_budget(self, new_request=True):
        if time.monotonic() >= self.deadline:
            raise BudgetExhausted("Edition time budget exhausted")
        if (new_request and self.requests >= self.max_requests) or self.bytes >= self.max_bytes:
            raise BudgetExhausted("Edition download budget exhausted")

    def get(self, url, limit=1_500_000):
        if time.monotonic() >= self.deadline:
            raise BudgetExhausted("Edition time budget exhausted")
        url = canonical_url(url)
        if url in self.cache:
            if len(self.cache[url][0]) > limit:
                raise ValueError("Download exceeds byte limit")
            self.cache.move_to_end(url)
            return self.cache[url]
        original = url
        for _ in range(5):
            self.check_budget()
            url = public_url(url)
            self.requests += 1
            with requests.get(url, timeout=(5, 12), stream=True, allow_redirects=False,
                              headers={"User-Agent": "MadsMorgen/1.1 (personal news reader)"}) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["Location"])
                    continue
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_content(16384):
                    self.check_budget(new_request=False)
                    self.bytes += len(chunk)
                    content.extend(chunk)
                    if self.bytes > self.max_bytes:
                        raise BudgetExhausted("Edition download byte budget exhausted")
                    if len(content) > limit:
                        raise ValueError("Download exceeds byte limit")
                result = (bytes(content), response.headers.get("Content-Type", ""), url)
                size = len(content)
                if size <= min(self.max_cache_item_bytes, self.max_cache_bytes):
                    while self.cache and self.cache_bytes + size > self.max_cache_bytes:
                        _, expired = self.cache.popitem(last=False)
                        self.cache_bytes -= len(expired[0])
                    self.cache[original] = result
                    self.cache_bytes += size
                return result
        raise ValueError("Too many redirects")
