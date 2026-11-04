from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

from locale_en import (
    english_budget,
    english_country,
    english_last_reply,
    english_member_since,
    english_published,
    normalize_stat_label,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
USER_AGENT_LINUX = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _browser_user_agent() -> str:
    if sys.platform.startswith("linux"):
        return USER_AGENT_LINUX
    return USER_AGENT

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


def _sidebar_has_client_stats(sidebar: dict[str, Any]) -> bool:
    return any(
        sidebar.get(key) is not None
        for key in (
            "published_projects",
            "projects_paid",
            "interested_freelancers",
            "bids",
        )
    )


def _apply_sidebar_to_job(job: WorkanaJob, sidebar: dict[str, Any]) -> WorkanaJob:
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


_PLAYWRIGHT_JOB_DETAIL_JS = """
() => {
  const root =
    document.querySelector('.project-details-sidebar') ||
    document.querySelector('.sidebar-project') ||
    document.querySelector('.project-sidebar') ||
    document.querySelector('aside') ||
    document.body;

  const stats = {};
  root.querySelectorAll('p.h4').forEach((h4) => {
    const num = h4.textContent.trim();
    const labelEl = h4.nextElementSibling;
    if (labelEl && labelEl.tagName === 'P' && /^\\d+$/.test(num)) {
      stats[labelEl.textContent.trim()] = parseInt(num, 10);
    }
  });

  const nameEl =
    root.querySelector('.h4.user-name span') ||
    root.querySelector('h4.user-name span') ||
    document.querySelector('.h4.user-name span');
  const starsEl =
    root.querySelector('.stars-fill') ||
    document.querySelector('.profile-stars .stars-fill');
  const flagEl =
    root.querySelector('[class*="flag-"]') ||
    document.querySelector('[class*="flag-"]');

  let countryCode = '';
  if (flagEl && flagEl.classList) {
    for (const cls of flagEl.classList) {
      if (cls.startsWith('flag-') && cls.length === 7) {
        countryCode = cls.slice(5).toUpperCase();
        break;
      }
    }
  }

  let memberSince = '';
  for (const p of root.querySelectorAll('p')) {
    const t = p.textContent.trim();
    if (/^(Member since|Miembro desde|Membro desde)/i.test(t)) {
      memberSince = t.split(':').slice(1).join(':').trim();
      break;
    }
  }

  return {
    stats,
    clientName: nameEl ? nameEl.textContent.trim() : '',
    ratingWidth: starsEl ? starsEl.style.width : '',
    countryCode,
    memberSince,
  };
}
"""


def _sidebar_from_playwright_data(data: dict[str, Any]) -> dict[str, Any]:
    stats: dict[str, int] = {}
    for label, value in (data.get("stats") or {}).items():
        try:
            stats[normalize_stat_label(str(label))] = int(value)
        except (TypeError, ValueError):
            continue

    rating: float | None = None
    width = str(data.get("ratingWidth") or "")
    width_match = re.search(r"([\d.]+)", width)
    if width_match:
        rating = float(width_match.group(1)) / 100 * 5

    return {
        "client_name": str(data.get("clientName") or "").strip(),
        "client_rating": rating,
        "client_country_code": str(data.get("countryCode") or "").strip(),
        "published_projects": stats.get("published projects"),
        "projects_paid": stats.get("projects paid"),
        "member_since": english_member_since(str(data.get("memberSince") or "")),
        "bids": stats.get("bids"),
        "interested_freelancers": stats.get("interested freelancers"),
    }


def _enrich_job_via_playwright(job: WorkanaJob, *, proxy: str | None = None) -> WorkanaJob:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("playwright is not installed.") from exc

    wait_sec = min(
        float(os.environ.get("WORKANA_PLAYWRIGHT_WAIT_SECONDS", "180")), 120.0
    )

    with sync_playwright() as playwright:
        launch_opts: dict[str, Any] = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            "ignore_default_args": ["--enable-automation"],
        }
        if proxy:
            launch_opts["proxy"] = {"server": proxy}
        browser = playwright.chromium.launch(**launch_opts)
        try:
            context = browser.new_context(
                user_agent=_browser_user_agent(),
                locale="en-US",
                viewport={"width": 1280, "height": 720},
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )
            page = context.new_page()
            page.goto(job.url, wait_until="load", timeout=120_000)
            _wait_for_playwright_content(
                page,
                jobs_listing=False,
                timeout_sec=wait_sec,
            )
            data = page.evaluate(_PLAYWRIGHT_JOB_DETAIL_JS)
            sidebar = _sidebar_from_playwright_data(data)
            if not _sidebar_has_client_stats(sidebar):
                sidebar = _parse_job_sidebar(page.content())
            return _apply_sidebar_to_job(job, sidebar)
        finally:
            browser.close()


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

# Windows curl often exits 61 (unsupported Brotli) when br is advertised.
CURL_HEADERS = {**DEFAULT_HEADERS, "Accept-Encoding": "gzip, deflate"}


@dataclass(frozen=True)
class WorkanaFetchConfig:
    use_playwright: bool = False
    playwright_fallback: bool = False
    remote_fetch_url: str | None = None
    remote_fetch_secret: str | None = None
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
        for key, value in CURL_HEADERS.items():
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
            if result.returncode == 61:
                last_detail = "exit 61 (Brotli encoding — use gzip-only curl headers)"
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


def _listing_has_dom_cards(page_html: str) -> bool:
    return 'class="project-item' in page_html


def _listing_html_ready(page_html: str) -> bool:
    if _is_cloudflare_challenge(page_html):
        return False
    if "results-initials" in page_html:
        return True
    return _listing_has_dom_cards(page_html)


def _dom_plain_text(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def _dom_first(pattern: re.Pattern[str], block: str, group: int = 1) -> str:
    match = pattern.search(block)
    return match.group(group).strip() if match else ""


_DOM_JOB_HREF_RE = re.compile(
    r'href="(?:https://www\.workana\.com)?/job/([^"#?]+)"',
    re.IGNORECASE,
)
_DOM_TITLE_RE = re.compile(
    r'class="h3 project-title"[^>]*>.*?title="([^"]+)"',
    re.DOTALL | re.IGNORECASE,
)
_DOM_BUDGET_RE = re.compile(r'class="values"[^>]*>([^<]+)', re.IGNORECASE)
_DOM_BIDS_RE = re.compile(
    r'class="bids"[^>]*>.*?(?:Propuestas|Bids|Propostas)\s*:\s*(\d+)',
    re.DOTALL | re.IGNORECASE,
)
_DOM_DATE_RE = re.compile(
    r'class="date"[^>]*>(?:\s*<[^>]+>)*\s*([^<]+)',
    re.DOTALL | re.IGNORECASE,
)
_DOM_DESC_RE = re.compile(
    r'class="text-expander-content"[^>]*>(.*?)</p>',
    re.DOTALL | re.IGNORECASE,
)
_DOM_SKILL_RE = re.compile(
    r'class="skill label[^"]*"[^>]*>([^<]+)',
    re.IGNORECASE,
)
_DOM_CLIENT_RE = re.compile(
    r'class="author-info user-name"[^>]*>.*?<span>([^<]+)</span>',
    re.DOTALL | re.IGNORECASE,
)
_DOM_COUNTRY_RE = re.compile(
    r'class="country-name"[^>]*>.*?>([^<]+)<',
    re.DOTALL | re.IGNORECASE,
)
_DOM_FLAG_RE = re.compile(r'class="flag flag-([a-z]{2})', re.IGNORECASE)
_DOM_STARS_RE = re.compile(
    r'class="stars-fill"[^>]*style="width:\s*([\d.]+)%',
    re.IGNORECASE,
)
_DOM_LAST_REPLY_RE = re.compile(
    r'class="message-created"[^>]*>.*?<span>([^<]+)</span>\s*<span>([^<]+)</span>',
    re.DOTALL | re.IGNORECASE,
)


def _job_from_dom_block(block: str) -> WorkanaJob | None:
    slug = _dom_first(_DOM_JOB_HREF_RE, block)
    if not slug:
        return None

    title = html.unescape(_dom_first(_DOM_TITLE_RE, block) or slug)
    budget_raw = _dom_plain_text(_dom_first(_DOM_BUDGET_RE, block))
    bids_match = _DOM_BIDS_RE.search(block)
    bids = int(bids_match.group(1)) if bids_match else 0

    published_raw = _dom_plain_text(_dom_first(_DOM_DATE_RE, block))

    desc_html = _DOM_DESC_RE.search(block)
    description_snippet = _description_snippet(
        _dom_plain_text(desc_html.group(1)) if desc_html else ""
    )

    skills = tuple(m.group(1).strip() for m in _DOM_SKILL_RE.finditer(block))
    if not skills:
        skills = tuple(
            m.group(1).strip()
            for m in re.finditer(r'class="skill[^"]*"[^>]*>([^<]+)', block, re.I)
        )

    client_name = html.unescape(_dom_first(_DOM_CLIENT_RE, block))
    country_raw = html.unescape(_dom_first(_DOM_COUNTRY_RE, block))

    flag_match = _DOM_FLAG_RE.search(block)
    client_country_code = flag_match.group(1).upper() if flag_match else ""

    rating: float | None = None
    stars_match = _DOM_STARS_RE.search(block)
    if stars_match:
        rating = float(stars_match.group(1)) / 100 * 5

    last_reply = ""
    reply_match = _DOM_LAST_REPLY_RE.search(block)
    if reply_match:
        last_reply = reply_match.group(2).strip()

    if not budget_raw:
        budget_match = re.search(
            r'class="budget[^"]*"[^>]*>.*?class="values"[^>]*>([^<]+)',
            block,
            re.DOTALL | re.IGNORECASE,
        )
        if budget_match:
            budget_raw = budget_match.group(1).strip()

    return WorkanaJob(
        slug=slug,
        title=title,
        url=f"https://www.workana.com/job/{slug}",
        budget=budget_raw,
        published=published_raw,
        bids=bids,
        country=country_raw,
        skills=skills,
        is_urgent="label-urgent" in block.lower() or "urgent" in block[:500].lower(),
        verified_payment="payment-verified" in block,
        description_snippet=description_snippet,
        client_name=client_name,
        client_rating=rating,
        client_country_code=client_country_code,
        last_reply=last_reply,
    )


_PLAYWRIGHT_LISTING_JS = """
() => Array.from(document.querySelectorAll('.project-item')).map((card) => {
  const link = card.querySelector('.project-title a[href*="/job/"]');
  const href = link ? link.getAttribute('href') : '';
  const slugMatch = href.match(/\\/job\\/([^/?#]+)/);
  const titleNode = card.querySelector('.project-title [title]');
  const budgetNode =
    card.querySelector('.budget .values') ||
    card.querySelector('p.budget .values') ||
    card.querySelector('.budget');
  const dateNode =
    card.querySelector('.project-main-details .date') ||
    card.querySelector('.project-body .date') ||
    card.querySelector('.date');
  const bidsNode = card.querySelector('.bids');
  const descNode = card.querySelector('.text-expander-content');
  const clientNode =
    card.querySelector('.author-info.user-name button span') ||
    card.querySelector('.author-info.user-name span') ||
    card.querySelector('.user-name span');
  const countryNode = card.querySelector('.country-name');
  const flagNode = card.querySelector('[class*="flag-"]');
  let countryCode = '';
  if (flagNode && flagNode.classList) {
    for (const cls of flagNode.classList) {
      if (cls.startsWith('flag-') && cls.length === 7) {
        countryCode = cls.slice(5).toUpperCase();
        break;
      }
    }
  }
  const starsNode = card.querySelector('.stars-fill');
  const msgNode = card.querySelector('.message-created');
  let lastReply = '';
  if (msgNode) {
    const spans = msgNode.querySelectorAll('span');
    if (spans.length) {
      lastReply = spans[spans.length - 1].textContent.trim();
    }
  }
  return {
    slug: slugMatch ? slugMatch[1] : '',
    title: (titleNode && titleNode.getAttribute('title')) ||
      (link && link.textContent.trim()) || '',
    budget: budgetNode ? budgetNode.textContent.trim() : '',
    published: dateNode ? dateNode.textContent.trim() : '',
    bidsText: bidsNode ? bidsNode.textContent.trim() : '',
    description: descNode ? descNode.textContent.trim() : '',
    skills: Array.from(card.querySelectorAll('a.skill'))
      .map((a) => a.textContent.trim())
      .filter(Boolean),
    clientName: clientNode ? clientNode.textContent.trim() : '',
    country: countryNode ? countryNode.textContent.trim() : '',
    countryCode,
    ratingWidth: starsNode ? starsNode.style.width : '',
    lastReply,
    verifiedPayment: !!card.querySelector('.payment-verified'),
    isUrgent: card.classList.contains('urgent') ||
      !!card.querySelector('.label-urgent, .urgent'),
  };
}).filter((item) => item.slug)
"""


def _job_from_playwright_record(record: dict[str, Any]) -> WorkanaJob:
    slug = str(record.get("slug") or "").strip()
    rating: float | None = None
    width = str(record.get("ratingWidth") or "")
    width_match = re.search(r"([\d.]+)", width)
    if width_match:
        rating = float(width_match.group(1)) / 100 * 5

    description = str(record.get("description") or "").strip()
    return WorkanaJob(
        slug=slug,
        title=html.unescape(str(record.get("title") or slug)),
        url=f"https://www.workana.com/job/{slug}",
        budget=str(record.get("budget") or "").strip(),
        published=str(record.get("published") or "").strip(),
        bids=_parse_bids(str(record.get("bidsText") or "")),
        country=str(record.get("country") or "").strip(),
        skills=tuple(record.get("skills") or ()),
        is_urgent=bool(record.get("isUrgent")),
        verified_payment=bool(record.get("verifiedPayment")),
        description_snippet=_description_snippet(description),
        client_name=str(record.get("clientName") or "").strip(),
        client_rating=rating,
        client_country_code=str(record.get("countryCode") or "").strip(),
        last_reply=str(record.get("lastReply") or "").strip(),
    )


def _fetch_listing_jobs_via_playwright(
    url: str, *, proxy: str | None = None
) -> list[WorkanaJob]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "WORKANA_USE_PLAYWRIGHT is enabled but playwright is not installed."
        ) from exc

    wait_sec = float(os.environ.get("WORKANA_PLAYWRIGHT_WAIT_SECONDS", "180"))

    with sync_playwright() as playwright:
        launch_opts: dict[str, Any] = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            "ignore_default_args": ["--enable-automation"],
        }
        if proxy:
            launch_opts["proxy"] = {"server": proxy}
        browser = playwright.chromium.launch(**launch_opts)
        try:
            context = browser.new_context(
                user_agent=_browser_user_agent(),
                locale="en-US",
                viewport={"width": 1280, "height": 720},
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )
            page = context.new_page()
            page.goto(url, wait_until="load", timeout=120_000)
            _wait_for_playwright_content(
                page,
                jobs_listing=True,
                timeout_sec=wait_sec,
            )
            records = page.evaluate(_PLAYWRIGHT_LISTING_JS)
            jobs = [_job_from_playwright_record(item) for item in records]
            if not jobs:
                raise RuntimeError("Playwright found no jobs in .project-item cards")
            return jobs
        finally:
            browser.close()


def _parse_jobs_from_dom_html(page_html: str) -> list[WorkanaJob]:
    chunks = re.split(r"(?=<div class=\"project-item\b)", page_html, flags=re.IGNORECASE)
    jobs: list[WorkanaJob] = []
    seen: set[str] = set()
    for chunk in chunks:
        if "project-item" not in chunk[:80].lower():
            continue
        job = _job_from_dom_block(chunk)
        if job and job.slug not in seen:
            seen.add(job.slug)
            jobs.append(job)
    return jobs


def _wait_for_playwright_content(
    page: Any, *, jobs_listing: bool, timeout_sec: float = 180.0
) -> str:
    deadline = time.monotonic() + timeout_sec
    last_log = 0.0
    while time.monotonic() < deadline:
        content = page.content()
        if jobs_listing:
            if _listing_html_ready(content):
                return content
        elif 'class="h4 user-name"' in content and not _is_cloudflare_challenge(content):
            return content

        now = time.monotonic()
        if now - last_log >= 20:
            log.info(
                "Playwright waiting for Workana (cloudflare=%s, listing_json=%s, dom_cards=%s)",
                _is_cloudflare_challenge(content),
                "results-initials" in content,
                _listing_has_dom_cards(content),
            )
            last_log = now
        page.wait_for_timeout(2500)

    content = page.content()
    if _is_cloudflare_challenge(content):
        raise RuntimeError(
            "Cloudflare did not clear within "
            f"{int(timeout_sec)}s on this server IP. Use a residential "
            "WORKANA_PROXY, run the bot on a home PC, or try another VPS region."
        )
    if jobs_listing and not _listing_html_ready(content):
        raise RuntimeError(
            "Workana loaded in Chromium but job listing data never appeared "
            f"(waited {int(timeout_sec)}s). Datacenter IPs are often blocked — "
            "set WORKANA_PROXY to a residential HTTP proxy or run the bot at home."
        )
    return content


def _fetch_html_via_playwright(url: str, *, proxy: str | None = None) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "WORKANA_USE_PLAYWRIGHT is enabled but playwright is not installed. "
            "On the VPS run: pip install playwright && playwright install chromium "
            "&& playwright install-deps"
        ) from exc

    wait_sec = float(os.environ.get("WORKANA_PLAYWRIGHT_WAIT_SECONDS", "180"))

    with sync_playwright() as playwright:
        launch_opts: dict[str, Any] = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            "ignore_default_args": ["--enable-automation"],
        }
        if proxy:
            launch_opts["proxy"] = {"server": proxy}
        browser = playwright.chromium.launch(**launch_opts)
        try:
            context = browser.new_context(
                user_agent=_browser_user_agent(),
                locale="en-US",
                viewport={"width": 1280, "height": 720},
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )
            page = context.new_page()
            page.goto(url, wait_until="load", timeout=120_000)
            return _wait_for_playwright_content(
                page,
                jobs_listing=_is_jobs_listing_url(url),
                timeout_sec=wait_sec,
            )
        finally:
            browser.close()


def _lightweight_html_usable(page_html: str, url: str) -> bool:
    if _is_cloudflare_challenge(page_html):
        return False
    if _is_jobs_listing_url(url):
        return _listing_html_ready(page_html)
    return bool(page_html.strip())


def _parse_jobs_page(page_html: str) -> list[WorkanaJob]:
    if _is_cloudflare_challenge(page_html):
        raise RuntimeError(
            "Workana returned a Cloudflare challenge. On a VPS, set "
            "WORKANA_USE_PLAYWRIGHT=true (see README) or use WORKANA_PROXY."
        )
    try:
        payload = _extract_results_json(page_html)
        results = payload.get("results") or []
        return [_job_from_result(item) for item in results]
    except ValueError:
        dom_jobs = _parse_jobs_from_dom_html(page_html)
        if dom_jobs:
            log.info(
                "Parsed %s jobs from listing page DOM (Vue render, no JSON embed)",
                len(dom_jobs),
            )
            return dom_jobs

        hint = ""
        if "results-initials" not in page_html and not _listing_has_dom_cards(page_html):
            hint = (
                " Use a listing URL with a locale prefix, e.g. "
                "https://www.workana.com/en/jobs?..."
            )
        raise RuntimeError(
            "Could not read job listings (blocked or page layout changed)."
            + hint
        )


class WorkanaFetchError(RuntimeError):
    pass


async def _fetch_page_html_playwright(url: str, *, proxy: str | None) -> str:
    log.info("Fetching Workana via Playwright (headless Chromium)")
    return await asyncio.to_thread(_fetch_html_via_playwright, url, proxy=proxy)


async def _fetch_html_via_remote(
    url: str, *, base_url: str, secret: str | None
) -> str:
    endpoint = base_url.rstrip("/") + "/fetch"
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    timeout = httpx.Timeout(180.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(endpoint, json={"url": url}, headers=headers)

    if response.status_code >= 400:
        detail = response.text[:300]
        try:
            detail = str(response.json().get("error", detail))
        except Exception:
            pass
        raise WorkanaFetchError(
            f"Home fetch bridge error HTTP {response.status_code}: {detail}"
        )

    data = response.json()
    page_html = str(data.get("html") or "")
    if _is_jobs_listing_url(url) and not _listing_html_ready(page_html):
        raise WorkanaFetchError(
            "Home fetch bridge returned a page without job listings "
            "(check Workana on the home PC)."
        )
    if not page_html.strip():
        raise WorkanaFetchError("Home fetch bridge returned empty HTML.")
    return page_html


async def _fetch_page_html_lightweight(url: str, *, proxy: str | None) -> str:
    if _curl_executable():
        try:
            return await asyncio.to_thread(_fetch_html_via_curl, url, proxy=proxy)
        except RuntimeError as exc:
            log.warning("curl fetch failed, falling back to httpx: %s", exc)

    try:
        return await _fetch_html_via_httpx(url, proxy=proxy)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (403, 429):
            raise WorkanaFetchError(
                f"Workana blocked the request (HTTP {exc.response.status_code})."
            ) from exc
        raise


async def fetch_page_html(url: str) -> str:
    cfg = _fetch_config
    proxy = cfg.proxy

    if cfg.remote_fetch_url:
        log.info("Workana fetch: home PC bridge (%s)", cfg.remote_fetch_url)
        return await _fetch_html_via_remote(
            url,
            base_url=cfg.remote_fetch_url,
            secret=cfg.remote_fetch_secret,
        )

    if cfg.use_playwright:
        try:
            html = await _fetch_page_html_lightweight(url, proxy=proxy)
            if _lightweight_html_usable(html, url):
                log.info("Workana fetch: curl/httpx OK (skipped Playwright)")
                return html
        except Exception as exc:
            log.debug("Lightweight fetch before Playwright failed: %s", exc)
        return await _fetch_page_html_playwright(url, proxy=proxy)

    try:
        return await _fetch_page_html_lightweight(url, proxy=proxy)
    except (WorkanaFetchError, RuntimeError) as exc:
        if not cfg.playwright_fallback:
            raise WorkanaFetchError(
                f"{exc} Set WORKANA_PLAYWRIGHT_FALLBACK=true or "
                "WORKANA_USE_PLAYWRIGHT=true in .env (requires playwright)."
            ) from exc
        log.warning("Lightweight fetch failed (%s), trying Playwright", exc)
        return await _fetch_page_html_playwright(url, proxy=proxy)


async def enrich_job_from_detail(job: WorkanaJob) -> WorkanaJob:
    cfg = _fetch_config

    if cfg.use_playwright and not cfg.remote_fetch_url:
        enriched = await asyncio.to_thread(
            _enrich_job_via_playwright, job, proxy=cfg.proxy
        )
        if _sidebar_has_client_stats(
            {
                "published_projects": enriched.published_projects,
                "projects_paid": enriched.projects_paid,
                "interested_freelancers": enriched.interested_freelancers,
                "bids": enriched.bids,
            }
        ):
            log.info("Loaded client stats from job page for %s", job.slug)
        return enriched

    page_html = await fetch_page_html(job.url)
    sidebar = _parse_job_sidebar(page_html)
    if (
        cfg.playwright_fallback
        and not cfg.remote_fetch_url
        and not _sidebar_has_client_stats(sidebar)
    ):
        try:
            enriched = await asyncio.to_thread(
                _enrich_job_via_playwright, job, proxy=cfg.proxy
            )
            log.debug("Client stats loaded via Playwright fallback for %s", job.slug)
            return enriched
        except Exception as exc:
            log.warning(
                "Playwright client enrich failed for %s: %s",
                job.slug,
                exc,
            )

    return _apply_sidebar_to_job(job, sidebar)


async def fetch_recent_jobs(jobs_url: str) -> list[WorkanaJob]:
    cfg = _fetch_config
    if (
        cfg.use_playwright
        and not cfg.remote_fetch_url
        and _is_jobs_listing_url(jobs_url)
    ):
        try:
            jobs = await asyncio.to_thread(
                _fetch_listing_jobs_via_playwright, jobs_url, proxy=cfg.proxy
            )
            log.info(
                "Parsed %s jobs via Playwright card extractor (full listing fields)",
                len(jobs),
            )
            return jobs
        except Exception as exc:
            log.warning(
                "Playwright card extractor failed, falling back to HTML parse: %s",
                exc,
            )

    page_html = await fetch_page_html(jobs_url)
    return _parse_jobs_page(page_html)
