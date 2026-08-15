"""Sitemap discovery mechanics.

Each case here is a real thing a payer does, not a hypothetical. Discovery
replaced hardcoded paths precisely because payers reorganise, and it is only an
improvement if it survives the ways they publish sitemaps badly.
"""

from __future__ import annotations

import gzip
import re

import pytest

from app.utils.sitemap import (
    SitemapEntry,
    collect_urls,
    filter_urls,
    parse_robots_sitemaps,
    parse_sitemap,
)


def test_declared_sitemap_is_used():
    robots = "User-agent: *\nSitemap: https://example.test/sitemap.xml\n"
    assert parse_robots_sitemaps(robots, "https://example.test") == [
        "https://example.test/sitemap.xml"
    ]


def test_missing_declaration_falls_back_to_the_conventional_path():
    assert parse_robots_sitemaps("User-agent: *\n", "https://example.test") == [
        "https://example.test/sitemap.xml"
    ]


def test_scheme_less_declaration_resolves_to_the_host():
    """Blue Cross of Illinois publishes `Sitemap:bcbsil.com/sitemap.xml`.

    No space, no scheme. `urljoin` treats that as a path and produces
    `https://www.bcbsil.com/bcbsil.com/sitemap.xml`, which returns 503 — a
    failure that looks like the payer being down rather than like a parsing bug
    on our side, and so gets investigated in the wrong place.
    """
    assert parse_robots_sitemaps(
        "Sitemap:bcbsil.com/sitemap.xml", "https://www.bcbsil.com"
    ) == ["https://bcbsil.com/sitemap.xml"]


def test_protocol_relative_declaration_keeps_the_scheme():
    assert parse_robots_sitemaps(
        "Sitemap: //cdn.example.test/sitemap.xml", "https://example.test"
    ) == ["https://cdn.example.test/sitemap.xml"]


def test_robots_txt_may_arrive_as_bytes():
    """Some payers serve robots.txt with a content type that leaves it undecoded."""
    assert parse_robots_sitemaps(
        b"Sitemap: https://example.test/a.xml", "https://example.test"
    ) == ["https://example.test/a.xml"]


def test_gzipped_sitemap_without_the_extension_is_decompressed():
    body = gzip.compress(
        b"<urlset><url><loc>https://example.test/a</loc></url></urlset>"
    )
    entries, is_index = parse_sitemap(body)
    assert is_index is False
    assert [e.url for e in entries] == ["https://example.test/a"]


def test_index_is_reported_rather_than_guessed_at():
    entries, is_index = parse_sitemap(
        "<sitemapindex><sitemap><loc>https://example.test/child.xml</loc>"
        "</sitemap></sitemapindex>"
    )
    assert is_index is True
    assert [e.url for e in entries] == ["https://example.test/child.xml"]


@pytest.mark.asyncio
async def test_collect_follows_an_index_and_dedupes():
    pages = {
        "https://example.test/robots.txt": "Sitemap: https://example.test/i.xml",
        "https://example.test/i.xml": (
            "<sitemapindex>"
            "<sitemap><loc>https://example.test/a.xml</loc></sitemap>"
            "<sitemap><loc>https://example.test/b.xml</loc></sitemap>"
            "</sitemapindex>"
        ),
        "https://example.test/a.xml": (
            "<urlset><url><loc>https://example.test/one</loc>"
            "<lastmod>2026-08-01</lastmod></url></urlset>"
        ),
        # Deliberately repeats /one: payers split sitemaps by section and the
        # same page appears in more than one.
        "https://example.test/b.xml": (
            "<urlset><url><loc>https://example.test/one</loc></url>"
            "<url><loc>https://example.test/two</loc></url></urlset>"
        ),
    }

    async def fetch(url: str) -> str:
        return pages[url]

    entries = await collect_urls(fetch, "https://example.test")
    assert [e.url for e in entries] == [
        "https://example.test/one",
        "https://example.test/two",
    ]
    # First sighting wins, so the lastmod is not lost to the barer duplicate.
    assert entries[0].lastmod == "2026-08-01"


@pytest.mark.asyncio
async def test_one_unreachable_child_costs_only_its_own_urls():
    pages = {
        "https://example.test/robots.txt": "Sitemap: https://example.test/i.xml",
        "https://example.test/i.xml": (
            "<sitemapindex>"
            "<sitemap><loc>https://example.test/ok.xml</loc></sitemap>"
            "<sitemap><loc>https://example.test/gone.xml</loc></sitemap>"
            "</sitemapindex>"
        ),
        "https://example.test/ok.xml": (
            "<urlset><url><loc>https://example.test/kept</loc></url></urlset>"
        ),
    }

    async def fetch(url: str) -> str:
        if url not in pages:
            raise RuntimeError("404")
        return pages[url]

    entries = await collect_urls(fetch, "https://example.test")
    assert [e.url for e in entries] == ["https://example.test/kept"]


@pytest.mark.asyncio
async def test_unreachable_robots_yields_nothing_rather_than_raising():
    """A payer we cannot reach must produce an empty crawl, not a failed one.

    And emphatically not a fallback to guessed paths — guessing is what
    discovery exists to replace.
    """

    async def fetch(url: str) -> str:
        raise RuntimeError("connection reset")

    assert await collect_urls(fetch, "https://example.test") == []


def test_filter_requires_the_declared_host():
    """A CMS that syndicates content points a sitemap at somebody else's domain.

    Without the check, an adapter politely crawls a third party under our own
    user agent — which is our name on somebody else's access log.
    """
    entries = [
        SitemapEntry("https://example.test/medical-policy/a"),
        SitemapEntry("https://partner.test/medical-policy/b"),
    ]
    kept = filter_urls(
        entries, [re.compile("medical-policy")], require_host="example.test"
    )
    assert [e.url for e in kept] == ["https://example.test/medical-policy/a"]


def test_exclude_beats_include():
    entries = [
        SitemapEntry("https://example.test/policies/a.html"),
        SitemapEntry("https://example.test/policies/a-redirect.html"),
    ]
    kept = filter_urls(
        entries,
        [re.compile("/policies/")],
        [re.compile(r"-redirect\.html$")],
    )
    assert [e.url for e in kept] == ["https://example.test/policies/a.html"]
