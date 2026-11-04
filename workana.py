from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

from locale_en import english_member_since, normalize_stat_label

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

RESULTS_INITIALS_RE = re.compile(
    r":results-initials='(\{.*?\})'\s*\n\s*:sneak-peek-config",
    re.DOTALL,
)

TITLE_FROM_HTML_RE = re.compile(r"<span title=\"([^\"]+)\"", re.IGNORECASE)
BIDS_RE = re.compile(r"(\d+)")

SIDEBAR_STAT_RE = re.compile(
    r'<p class="h4">(\d+)</p>\s*<p>([^<]+)</p>',
    re.IGNORECASE,
)
CLIENT_NAME_RE = re.compile(
    r'class="h4 user-name"[^>]*>\s*<span>([^<]+)</span>',
    re.IGNORECASE,
)
STARS_WIDTH_RE = re.compile(
    r'stars-fill"[^>]*style="width:\s*([\d.]+)%',
    re.IGNORECASE,
)
COUNTRY_FLAG_RE = re.compile(r'flag flag-([a-z]{2})', re.IGNORECASE)
MEMBER_SINCE_RE = re.compile(
    r"<p>(?:Member since|Miembro desde|Membro desde):\s*([^<]+)</p>",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WorkanaJob:
    slug: str
    title: str
    url: str
    budget: str
    published: str
    bids: int
    country: str
    skills: tuple[str, ...]
    is_urgent: bool
    verified_payment: bool
    description_snippet: str
    client_name: str = ""
    client_rating: float | None = None
    client_country_code: str = ""
    published_projects: int | None = None
    projects_paid: int | None = None
    member_since: str = ""
    interested_freelancers: int | None = None
    last_reply: str = ""

    def matches_keywords(self, keywords: list[str]) -> bool:
        if not keywords:
            return True
        haystack = " ".join(
            [self.title, self.description_snippet, " ".join(self.skills)]
        ).lower()
        return any(k in haystack for k in keywords)

    def matches_max_bids(self, max_bids: int | None) -> bool:
        if max_bids is None:
            return True
        return self.bids <= max_bids


def _plain_title(raw_title: str) -> str:
    unescaped = html.unescape(raw_title)
    match = TITLE_FROM_HTML_RE.search(unescaped)
    if match:
        return html.unescape(match.group(1))
    text = re.sub(r"<[^>]+>", "", unescaped)
    return html.unescape(text).strip()


def _parse_bids(total_bids: str) -> int:
    match = BIDS_RE.search(total_bids or "")
    return int(match.group(1)) if match else 0


def _country_name(country_html: str) -> str:
    text = html.unescape(country_html or "")
    match = re.search(r'class="country-name"[^>]*>.*?>([^<]+)<', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r'title="([^"]+)"', text)
    return match.group(1).strip() if match else ""


def _country_code(country_html: str) -> str:
    match = COUNTRY_FLAG_RE.search(country_html or "")
    return match.group(1).upper() if match else ""


def _description_snippet(description: str, limit: int = 280) -> str:
    text = re.sub(r"<br\s*/?>", "\n", description or "", flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _rating_from_listing(raw: Any) -> float | None:
    if not isinstance(raw, dict):
        return None
    value = raw.get("value")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sidebar_stats(page_html: str) -> dict[str, int]:
    stats: dict[str, int] = {}
    for match in SIDEBAR_STAT_RE.finditer(page_html):
        label = normalize_stat_label(match.group(2))
        stats[label] = int(match.group(1))
    return stats


def _parse_job_sidebar(page_html: str) -> dict[str, Any]:
    stats = _sidebar_stats(page_html)
    name_match = CLIENT_NAME_RE.search(page_html)
    stars_match = STARS_WIDTH_RE.search(page_html)
    flag_match = COUNTRY_FLAG_RE.search(page_html)
    member_match = MEMBER_SINCE_RE.search(page_html)

    rating: float | None = None
    if stars_match:
        rating = float(stars_match.group(1)) / 100 * 5

    return {
        "client_name": name_match.group(1).strip() if name_match else "",
        "client_rating": rating,
        "client_country_code": (flag_match.group(1).upper() if flag_match else ""),
        "published_projects": stats.get("published projects"),
        "projects_paid": stats.get("projects paid"),
        "member_since": english_member_since(
            member_match.group(1).strip() if member_match else ""
        ),
        "bids": stats.get("bids"),
        "interested_freelancers": stats.get("interested freelancers"),
    }


def _job_from_result(item: dict[str, Any]) -> WorkanaJob:
    slug = item["slug"]
    skills = tuple(s.get("anchorText", "") for s in item.get("skills") or [])
    country_html = item.get("country", "")
    return WorkanaJob(
        slug=slug,
        title=_plain_title(item.get("title", slug)),
        url=f"https://www.workana.com/job/{slug}",
        budget=(item.get("budget") or "").strip(),
        published=(item.get("publishedDate") or item.get("postedDate") or "").strip(),
        bids=_parse_bids(item.get("totalBids", "")),
        country=_country_name(country_html),
        client_country_code=_country_code(country_html),
        skills=skills,
        is_urgent=bool(item.get("isUrgent")),
        verified_payment=bool(item.get("hasVerifiedPaymentMethod")),
        description_snippet=_description_snippet(item.get("description", "")),
        client_name=(item.get("authorName") or "").strip(),
        client_rating=_rating_from_listing(item.get("rating")),
        last_reply=(item.get("lastEmployerMessage") or "").strip(),
    )


def _extract_results_json_from_attribute(page_html: str) -> dict[str, Any] | None:
    key = ":results-initials="
    key_idx = page_html.find(key)
    if key_idx < 0:
        return None

    rest = page_html[key_idx + len(key) :].lstrip()
    if not rest or rest[0] not in ("'", '"'):
        return None
    quote = rest[0]

    sneak_idx = rest.find(":sneak-peek-config", 1)
    if sneak_idx < 0:
        return None

    chunk = rest[1:sneak_idx].rstrip()
    if chunk.endswith(quote):
        chunk = chunk[:-1]
    return json.loads(html.unescape(chunk))


def _extract_results_json(page_html: str) -> dict[str, Any]:
    try:
        payload = _extract_results_json_from_attribute(page_html)
        if payload is not None:
            return payload
    except json.JSONDecodeError:
        pass

    match = RESULTS_INITIALS_RE.search(page_html)
    if not match:
        raise ValueError(
            "Could not parse Workana job list (page layout may have changed)."
        )
    raw = html.unescape(match.group(1))
    return json.loads(raw)


DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


@dataclass(frozen=True)
class WorkanaFetchConfig:
    use_playwright: bool = False
    proxy: str | None = None


_fetch_config = WorkanaFetchConfig()


def configure_workana_fetch(config: WorkanaFetchConfig) -> None:
    global _fetch_config
    _fetch_config = config


def _curl_executable() -> str | None:
    custom = os.environ.get("WORKANA_CURL", "").strip()
    if custom:
        return custom
    for name in (
        "curl-impersonate-chrome",
        "curl_chrome116",
        "curl_chrome120",
        "curl.exe",
        "curl",
    ):
        path = shutil.which(name)
        if path:
            return path
    return None


def _is_cloudflare_challenge(page_html: str) -> bool:
    return "Just a moment" in page_html and "results-initials" not in page_html


def _is_jobs_listing_url(url: str) -> bool:
    path = urlparse(url).path or ""
    return re.search(r"/jobs/?$", path) or "/jobs" in path


def _fetch_html_via_curl(
    url: str, *, proxy: str | None = None, max_attempts: int = 3
) -> str:
    curl = _curl_executable()
    if not curl:
        raise RuntimeError("curl is not installed.")

    last_detail = "unknown error"
    for attempt in range(max_attempts):
        cmd = [
            curl,
            "-sL",
            "--compressed",
            "--connect-timeout",
            "30",
            "--max-time",
            "60",
            "-A",
            USER_AGENT,
        ]
        for key, value in DEFAULT_HEADERS.items():
            if key == "User-Agent":
                continue
            cmd.extend(["-H", f"{key}: {value}"])
        if proxy:
            cmd.extend(["-x", proxy])
        cmd.append(url)
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=75,
            check=False,
        )
        if result.returncode == 0:
            text = result.stdout.decode("utf-8", errors="replace")
            if text.strip():
                if _is_cloudflare_challenge(text):
                    last_detail = "Cloudflare challenge page"
                else:
                    return text
            else:
                last_detail = "empty response body"
        else:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            last_detail = f"exit {result.returncode}"
            if stderr:
                last_detail = f"{last_detail} ({stderr[:200]})"

        if attempt < max_attempts - 1:
            delay = 2 * (attempt + 1)
            log.warning(
                "Workana curl fetch failed (%s), retry in %ss (%s/%s)",
                last_detail,
                delay,
                attempt + 1,
                max_attempts,
            )
            time.sleep(delay)

    raise RuntimeError(
        f"curl failed after {max_attempts} attempts ({last_detail})"
    )


async def _fetch_html_via_httpx(url: str, *, proxy: str | None = None) -> str:
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(45.0),
        headers=DEFAULT_HEADERS,
        proxy=proxy,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


def _fetch_html_via_playwright(url: str, *, proxy: str | None = None) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "WORKANA_USE_PLAYWRIGHT is enabled but playwright is not installed. "
            "On the VPS run: pip install playwright && playwright install chromium "
            "&& playwright install-deps"
        ) from exc

    with sync_playwright() as playwright:
        launch_opts: dict[str, Any] = {"headless": True}
        if proxy:
            launch_opts["proxy"] = {"server": proxy}
        browser = playwright.chromium.launch(**launch_opts)
        try:
            context = browser.new_context(
                user_agent=USER_AGENT,
                locale="en-US",
                viewport={"width": 1280, "height": 720},
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            if _is_jobs_listing_url(url):
                page.wait_for_function(
                    """() => document.documentElement.innerHTML.includes('results-initials')""",
                    timeout=90_000,
                )
            else:
                try:
                    page.wait_for_function(
                        """() => document.documentElement.innerHTML.includes('class="h4 user-name"')""",
                        timeout=60_000,
                    )
                except Exception:
                    page.wait_for_timeout(8000)
            content = page.content()
            if _is_cloudflare_challenge(content):
                raise RuntimeError(
                    "Workana still returned Cloudflare after browser fetch. "
                    "Try a residential WORKANA_PROXY or run the bot from a home PC."
                )
            return content
        finally:
            browser.close()


def _parse_jobs_page(page_html: str) -> list[WorkanaJob]:
    if _is_cloudflare_challenge(page_html):
        raise RuntimeError(
            "Workana returned a Cloudflare challenge. On a VPS, set "
            "WORKANA_USE_PLAYWRIGHT=true (see README) or use WORKANA_PROXY."
        )
    try:
        payload = _extract_results_json(page_html)
    except ValueError as exc:
        hint = ""
        if "results-initials" not in page_html:
            hint = (
                " Use a listing URL with a locale prefix, e.g. "
                "https://www.workana.com/en/jobs?..."
            )
        raise RuntimeError(
            "Could not read job listings (blocked or page layout changed)."
            + hint
        ) from exc
    results = payload.get("results") or []
    return [_job_from_result(item) for item in results]


async def fetch_page_html(url: str) -> str:
    cfg = _fetch_config
    proxy = cfg.proxy

    if cfg.use_playwright:
        log.debug("Fetching via Playwright: %s", url)
        return await asyncio.to_thread(_fetch_html_via_playwright, url, proxy=proxy)

    if _curl_executable():
        try:
            return await asyncio.to_thread(
                _fetch_html_via_curl, url, proxy=proxy
            )
        except RuntimeError as exc:
            log.warning("curl fetch failed, falling back to httpx: %s", exc)

    try:
        return await _fetch_html_via_httpx(url, proxy=proxy)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in (403, 429):
            raise
        if not _curl_executable():
            raise RuntimeError(
                "Workana blocked the request (HTTP "
                f"{exc.response.status_code}). Install curl or try again later."
            ) from exc
        return await asyncio.to_thread(_fetch_html_via_curl, url, proxy=proxy)


async def enrich_job_from_detail(job: WorkanaJob) -> WorkanaJob:
    page_html = await fetch_page_html(job.url)
    sidebar = _parse_job_sidebar(page_html)
    updates: dict[str, Any] = {
        "published_projects": sidebar["published_projects"],
        "projects_paid": sidebar["projects_paid"],
        "member_since": sidebar["member_since"],
        "interested_freelancers": sidebar["interested_freelancers"],
    }
    if sidebar["client_name"]:
        updates["client_name"] = sidebar["client_name"]
    if sidebar["client_rating"] is not None:
        updates["client_rating"] = sidebar["client_rating"]
    if sidebar["client_country_code"]:
        updates["client_country_code"] = sidebar["client_country_code"]
    if sidebar["bids"] is not None:
        updates["bids"] = sidebar["bids"]
    return replace(job, **updates)


async def fetch_recent_jobs(jobs_url: str) -> list[WorkanaJob]:
    page_html = await fetch_page_html(jobs_url)
    return _parse_jobs_page(page_html)
