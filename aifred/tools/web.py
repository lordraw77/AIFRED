"""``web_fetch`` and ``web_search`` with an SSRF guard (spec 9, 11).

``web_fetch`` accepts only http/https, resolves the host and requires every address
to be globally routable (IPv4-mapped and NAT64 IPv6 forms included), follows
redirects by hand and re-checks each hop. The DNS check does not close the
DNS-rebinding window (accepted for personal use, see spec section 11).
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

MAX_BYTES = 1_048_576
MAX_REDIRECTS = 5
FETCH_TIMEOUT = 15.0
USER_AGENT = "AIFRED/0.1 (+personal agent; web_fetch)"
_TEXT_TYPES = ("text/", "application/json", "application/xml", "application/xhtml", "application/rss", "application/atom", "application/javascript")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")

Resolver = Callable[[str, int], list[str]]
ClientFactory = Callable[[], httpx.Client]


class BlockedURL(Exception):
    """The URL points somewhere web_fetch must not go."""


def default_resolver(host: str, port: int) -> list[str]:
    """Resolve a host name to its addresses."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise BlockedURL(f"cannot resolve {host!r}: {exc.strerror or exc}") from None
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def default_client() -> httpx.Client:
    """HTTP client that never follows redirects by itself."""
    return httpx.Client(follow_redirects=False, timeout=FETCH_TIMEOUT, headers={"User-Agent": USER_AGENT})


def is_public(address: str) -> bool:
    """Whether an address is globally routable (and not an alias of a private one)."""
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
    return ip.is_global and not ip.is_multicast


def check_url(url: str, *, allow_private: bool, resolver: Resolver) -> str:
    """Validate a URL and return it normalised.

    Raises:
        BlockedURL: For a bad scheme, credentials, no host, or a non-public address.
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise BlockedURL("only http and https URLs are allowed")
    if not parts.hostname:
        raise BlockedURL("the URL has no host")
    if parts.username or parts.password:
        raise BlockedURL("URLs with embedded credentials are not allowed")
    if not allow_private:
        host = parts.hostname.rstrip(".")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        addresses = [host] if _is_ip_literal(host) else resolver(host, port)
        if not addresses:
            raise BlockedURL(f"{host!r} did not resolve")
        for address in addresses:
            if not is_public(address):
                raise BlockedURL("the address is private, local or otherwise not public")
    return parts.geturl()


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "pre", "table", "ul", "ol", "blockquote", "hr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in self._SKIP and tag != "head":
            self._skip += 1
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in self._SKIP and tag != "head" and self._skip:
            self._skip -= 1
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    """Convert HTML to readable text.

    Returns:
        ``(title, text)``.
    """
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return " ".join(parser.title.split()), text.strip()


class WebTools:
    """Implementation behind the web tools; resolver and client are replaceable in tests."""

    def __init__(self, resolver: Resolver = default_resolver, client_factory: ClientFactory = default_client) -> None:
        self.resolver = resolver
        self.client_factory = client_factory

    def _get(self, url: str, allow_private: bool, handler: Callable[[httpx.Response, str], Any]) -> Any:
        """GET a URL, following redirects by hand and re-checking every hop, then hand the response to ``handler``.

        Raises:
            BlockedURL: If any hop is not allowed.
            ValueError: For network errors or too many redirects.
        """
        current = check_url(url, allow_private=allow_private, resolver=self.resolver)
        with self.client_factory() as client:
            for hop in range(MAX_REDIRECTS + 1):
                try:
                    with client.stream("GET", current) as response:
                        if response.status_code in (301, 302, 303, 307, 308) and "location" in response.headers:
                            if hop == MAX_REDIRECTS:
                                raise ValueError(f"more than {MAX_REDIRECTS} redirects")
                            current = check_url(
                                urljoin(current, response.headers["location"]),
                                allow_private=allow_private, resolver=self.resolver,
                            )
                            continue
                        return handler(response, current)
                except httpx.HTTPError as exc:
                    raise ValueError(f"request failed: {type(exc).__name__}: {exc}"[:300]) from None
        raise ValueError("too many redirects")  # pragma: no cover - loop always returns or raises

    def fetch(self, url: str, max_chars: int, *, allow_private: bool) -> str:
        """Fetch a page and return its text with a short header.

        Raises:
            BlockedURL: If any hop is not allowed.
            ValueError: For network errors, bad status or unsupported content.
        """
        result: str = self._get(url, allow_private, lambda response, current: self._read(response, current, max_chars))
        return result

    def fetch_raw(self, url: str, max_bytes: int, *, allow_private: bool) -> tuple[str, str, bytes]:
        """Download a document as bytes (for the knowledge base).

        Returns:
            The final URL, the content type (without parameters) and the body.

        Raises:
            BlockedURL: If any hop is not allowed.
            ValueError: For network errors, a bad status or a body larger than ``max_bytes``.
        """

        def read(response: httpx.Response, current: str) -> tuple[str, str, bytes]:
            if response.status_code >= 400:
                raise ValueError(f"HTTP {response.status_code} from {current}")
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise ValueError(f"the document is larger than {max_bytes:,} bytes")
            return current, response.headers.get("content-type", "").split(";")[0].strip().lower(), bytes(data)

        result: tuple[str, str, bytes] = self._get(url, allow_private, read)
        return result

    @staticmethod
    def _read(response: httpx.Response, url: str, max_chars: int) -> str:
        if response.status_code >= 400:
            raise ValueError(f"HTTP {response.status_code} from {url}")
        ctype = response.headers.get("content-type", "text/plain").split(";")[0].strip().lower()
        if not ctype.startswith(_TEXT_TYPES):
            raise ValueError(f"unsupported content type {ctype!r}")
        data = bytearray()
        truncated = False
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                del data[MAX_BYTES:]
                truncated = True
                break
        text = bytes(data).decode(response.charset_encoding or "utf-8", errors="replace")
        title = ""
        if ctype in ("text/html", "application/xhtml+xml"):
            title, text = html_to_text(text)
        head = f"URL: {url}\nStatus: {response.status_code}\nType: {ctype}\n" + (f"Title: {title}\n" if title else "")
        if truncated:
            head += "Note: the page was cut at 1 MB\n"
        if len(text) > max_chars:
            head += f"Note: showing the first {max_chars:,} of {len(text):,} characters\n"
            text = text[:max_chars]
        return f"{head}\n{text}"

    def search(self, base_url: str, query: str, limit: int = 8) -> str:
        """Query a SearXNG instance (JSON format) and format the top results."""
        url = base_url.rstrip("/") + "/search"
        try:
            with self.client_factory() as client:
                response = client.get(url, params={"q": query, "format": "json"})
        except httpx.HTTPError as exc:
            raise ValueError(f"search backend unreachable: {type(exc).__name__}"[:200]) from None
        if response.status_code >= 400:
            raise ValueError(f"search backend answered HTTP {response.status_code}")
        try:
            results: list[dict[str, Any]] = response.json().get("results", [])
        except ValueError:
            raise ValueError("search backend did not return JSON (is the json format enabled?)") from None
        if not results:
            return "No results."
        lines = []
        for i, r in enumerate(results[:limit], start=1):
            snippet = " ".join(str(r.get("content", "")).split())[:300]
            lines.append(f"{i}. {r.get('title', '').strip()}\n   {r.get('url', '')}\n   {snippet}")
        return "\n".join(lines)
