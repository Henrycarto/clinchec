"""Sitemap-driven URL discovery.

Hardcoded paths are a maintenance liability that compounds every time a payer
reorganises. The August 2026 crawl validation made that concrete: every UHC path
in the adapter was stale, and both BCBS licensee URLs were dead — one 404, one
redirecting to an error page **with HTTP 200**, which no status-code check would
have caught.

Payers publish a sitemap. It is authoritative, it is maintained by them, and it
survives exactly the reorganisation that breaks hardcoded paths. So discovery
walks robots.txt to the sitemap and filters by URL pattern, and an adapter
supplies only the patterns.

This module is deliberately generic. What each payer considers a policy page is
adapter knowledge; parsing XML is not.
"""

from __future__ import annotations

import gzip
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

#: An adapter's own fetch, passed in so rate limiting and robots.txt are
#: inherited rather than bypassed. Discovery must be as polite as crawling.
FetchFn = Callable[[str], Awaitable[str]]

_SITEMAP_DIRECTIVE_RE = re.compile(r"(?im)^\s*sitemap:\s*(\S+)\s*$")
_LOC_RE = re.compile(r"<loc>\s*([^<]+?)\s*</loc>", re.IGNORECASE)
_LASTMOD_RE = re.compile(r"<lastmod>\s*([^<]+?)\s*</lastmod>", re.IGNORECASE)
_IS_INDEX_RE = re.compile(r"<sitemapindex", re.IGNORECASE)

#: A sitemap index can fan out to hundreds of children. Following every one on
#: every crawl is neither useful nor polite.
DEFAULT_MAX_CHILD_SITEMAPS = 12

#: Guard against a payer publishing a sitemap with a million entries.
DEFAULT_MAX_ENTRIES = 50_000


@dataclass(frozen=True)
class SitemapEntry:
    url: str
    lastmod: str | None = None


def _decode(body: str | bytes) -> str:
    """Sitemaps are frequently served gzipped, sometimes without the extension."""
    if isinstance(body, str):
        return body
    if body[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(body).decode("utf-8", errors="replace")
        except OSError:
            return body.decode("utf-8", errors="replace")
    return body.decode("utf-8", errors="replace")

def parse_robots_sitemaps(robots_txt: str | bytes, base_url: str) -> list[str]:
    """Sitemap URLs declared in robots.txt.

    Accepts bytes as well as str: an adapter's fetch may hand back raw content,
    and some payers serve robots.txt with a content type that leaves it
    undecoded.

    Falls back to the conventional /sitemap.xml when none are declared — most
    sites have one even without advertising it.
    """
    found = [
        _resolve(match.group(1), base_url)
        for match in _SITEMAP_DIRECTIVE_RE.finditer(_decode(robots_txt))
    ]
    found = [url for url in found if url]
    if found:
        return list(dict.fromkeys(found))
    return [urljoin(base_url, "/sitemap.xml")]


#: A scheme-less value that starts with something host-shaped: "bcbsil.com/…".
_BARE_HOST_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}(?:/|$)", re.IGNORECASE)


def _resolve(value: str, base_url: str) -> str | None:
    """Resolve a robots.txt Sitemap value, tolerating malformed declarations.

    Real example, from BCBS of Illinois:

        Sitemap:bcbsil.com/sitemap.xml

    No space after the colon and no scheme. `urljoin` treats a scheme-less
    value as a path, producing `https://www.bcbsil.com/bcbsil.com/sitemap.xml`
    — a 503, and one that looks like the payer is down rather than like a
    parsing bug on our side.
    """
    value = value.strip()
    if not value:
        return None
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("//"):
        return f"{urlparse(base_url).scheme}:{value}"
    if _BARE_HOST_RE.match(value):
        return f"{urlparse(base_url).scheme}://{value}"
    return urljoin(base_url, value)



def parse_sitemap(body: str | bytes) -> tuple[list[SitemapEntry], bool]:
    """Return `(entries, is_index)` for one sitemap document.

    A `<sitemapindex>` lists other sitemaps rather than pages, and the caller
    has to recurse. The two documents are otherwise identical in shape, which is
    why the distinction is reported rather than guessed at by the caller.
    """
    text = _decode(body)
    locs = _LOC_RE.findall(text)
    mods = _LASTMOD_RE.findall(text)
    entries = [
        SitemapEntry(url=loc, lastmod=mods[i] if i < len(mods) else None)
        for i, loc in enumerate(locs)
    ]
    return entries, bool(_IS_INDEX_RE.search(text))


async def collect_urls(
    fetch: FetchFn,
    base_url: str,
    *,
    max_child_sitemaps: int = DEFAULT_MAX_CHILD_SITEMAPS,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> list[SitemapEntry]:
    """Every page URL a site publishes, via robots.txt and its sitemaps.

    Failures are per-document and never fatal: one unreachable child sitemap
    costs its slice of coverage, not the whole crawl. A payer with no reachable
    sitemap at all returns an empty list, and the adapter decides what to do —
    which should be "emit nothing" rather than "guess at paths", since guessing
    is what this exists to replace.
    """
    robots_url = urljoin(base_url, "/robots.txt")
    try:
        robots = await fetch(robots_url)
    except Exception as exc:  # noqa: BLE001 — discovery degrades, never raises
        logger.warning("sitemap: robots.txt unreachable at %s (%s)", robots_url, exc)
        robots = ""

    entries: list[SitemapEntry] = []
    seen: set[str] = set()
    queue = list(parse_robots_sitemaps(robots, base_url))
    followed_children = 0

    while queue and len(entries) < max_entries:
        sitemap_url = queue.pop(0)
        if sitemap_url in seen:
            continue
        seen.add(sitemap_url)

        try:
            body = await fetch(sitemap_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sitemap: %s unreachable (%s)", sitemap_url, exc)
            continue

        found, is_index = parse_sitemap(body)
        if is_index:
            room = max_child_sitemaps - followed_children
            if room <= 0:
                logger.warning(
                    "sitemap: child limit %d reached at %s; remaining indexes skipped",
                    max_child_sitemaps,
                    sitemap_url,
                )
                continue
            children = [e.url for e in found][:room]
            followed_children += len(children)
            if len(found) > room:
                logger.warning(
                    "sitemap: %s lists %d child sitemaps, following %d",
                    sitemap_url,
                    len(found),
                    room,
                )
            queue.extend(children)
        else:
            entries.extend(found)

    if len(entries) >= max_entries:
        logger.warning(
            "sitemap: entry cap %d reached for %s; discovery is incomplete",
            max_entries,
            base_url,
        )

    unique: dict[str, SitemapEntry] = {}
    for entry in entries:
        unique.setdefault(entry.url, entry)
    return list(unique.values())


def filter_urls(
    entries: list[SitemapEntry],
    include: list[re.Pattern[str]],
    exclude: list[re.Pattern[str]] | None = None,
    *,
    require_host: str | None = None,
) -> list[SitemapEntry]:
    """Entries matching any include pattern and no exclude pattern.

    `require_host` guards against a sitemap pointing off-site — a real hazard
    when a payer's CMS syndicates content, and one that would otherwise have an
    adapter politely crawling somebody else's domain.
    """
    exclude = exclude or []
    out: list[SitemapEntry] = []
    for entry in entries:
        if require_host and urlparse(entry.url).netloc != require_host:
            continue
        if not any(p.search(entry.url) for p in include):
            continue
        if any(p.search(entry.url) for p in exclude):
            continue
        out.append(entry)
    return out
