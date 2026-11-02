from __future__ import annotations

import asyncio
import logging
import sys

from telegram import Bot, CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError

from config import Settings
from locale_en import (
    english_budget,
    english_country,
    english_last_reply,
    english_member_since,
    english_published,
)
from storage import SeenJobStore
from workana import WorkanaJob, enrich_job_from_detail, fetch_recent_jobs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("workana-bot")


def _escape_md(text: str) -> str:
    text = text.replace("\u2014", "-").replace("\u2013", "-")
    for ch in ("\\", "_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"):
        text = text.replace(ch, f"\\{ch}")
    return text


def _country_flag_emoji(code: str) -> str:
    code = (code or "").upper()
    if len(code) != 2 or not code.isalpha():
        return ""
    return chr(0x1F1E6 + ord(code[0]) - ord("A")) + chr(
        0x1F1E6 + ord(code[1]) - ord("A")
    )


def _format_country(job: WorkanaJob) -> str:
    label = _escape_md(english_country(job.country))
    flag = _country_flag_emoji(job.client_country_code)
    if flag:
        return f"{flag} {label}"
    return f"🌎 {label}"


def _format_client_section(job: WorkanaJob) -> str:
    if not job.client_name and job.published_projects is None:
        return ""

    lines: list[str] = ["\n*Client*"]
    if job.client_name:
        flag = _country_flag_emoji(job.client_country_code)
        rating = (
            f"{job.client_rating:.1f}/5"
            if job.client_rating is not None
            else "—"
        )
        location = _format_country(job) if job.country else (
            f"{flag} {_escape_md(job.client_country_code)}"
            if flag
            else f"🌎 {_escape_md(job.client_country_code or '—')}"
        )
        lines.append(
            f"👤 {_escape_md(job.client_name)} · ⭐ {_escape_md(rating)} · "
            f"{location}"
        )

    stats: list[str] = []
    if job.published_projects is not None:
        stats.append(f"{job.published_projects} published")
    if job.projects_paid is not None:
        stats.append(f"{job.projects_paid} paid")
    if stats:
        lines.append(f"📊 {_escape_md(' · '.join(stats))}")
    if job.member_since:
        lines.append(
            f"📆 Member since: {_escape_md(english_member_since(job.member_since))}"
        )

    activity: list[str] = [f"{job.bids} bids"]
    if job.interested_freelancers is not None:
        activity.append(f"{job.interested_freelancers} interested")
    if job.interested_freelancers is not None or job.published_projects is not None:
        lines.append(f"📈 {_escape_md(' · '.join(activity))}")

    return "\n".join(lines) + "\n"


def format_job_message(job: WorkanaJob) -> str:
    budget = _escape_md(english_budget(job.budget))
    published = _escape_md(english_published(job.published))
    skills = _escape_md(", ".join(job.skills[:8]) if job.skills else "—")
    snippet = _escape_md(job.description_snippet)
    flags: list[str] = []
    if job.is_urgent:
        flags.append("URGENT")
    if job.verified_payment:
        flags.append("verified payment")
    flag_line = f"\n🏷 {_escape_md(' · '.join(flags))}" if flags else ""
    client_block = _format_client_section(job)
    last_reply = _escape_md(english_last_reply(job.last_reply))
    meta_parts = [
        f"📅 {published}",
        f"💬 {last_reply}",
        _format_country(job),
    ]
    meta_line = " · ".join(meta_parts)

    return (
        f"🆕 *New Workana job*\n\n"
        f"*{_escape_md(job.title)}*\n"
        f"💰 {budget}\n"
        f"{meta_line}\n"
        f"🛠 {skills}{flag_line}"
        f"{client_block}\n"
        f"{snippet}"
    )


def job_link_keyboard(job: WorkanaJob) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Open job ↗", url=job.url),
                InlineKeyboardButton(
                    "📋 Copy link",
                    copy_text=CopyTextButton(text=job.url),
                ),
            ]
        ]
    )


async def send_alert(bot: Bot, chat_id: str, job: WorkanaJob) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=format_job_message(job),
        parse_mode=ParseMode.MARKDOWN_V2,
        disable_web_page_preview=True,
        reply_markup=job_link_keyboard(job),
    )


async def poll_loop(settings: Settings) -> None:
    store = SeenJobStore(settings.data_dir / "seen_jobs.json")
    bot = Bot(token=settings.telegram_bot_token)

    log.info("Workana URL: %s", settings.workana_jobs_url)
    log.info("Poll every %s seconds", settings.poll_interval_seconds)
    if settings.keywords:
        log.info("Keywords: %s", ", ".join(settings.keywords))
    if settings.max_bids is not None:
        log.info("Max bids filter: %s", settings.max_bids)

    bootstrapped = False

    while True:
        try:
            jobs = await fetch_recent_jobs(settings.workana_jobs_url)
            log.info("Fetched %s jobs from listing page", len(jobs))

            if not bootstrapped:
                store.mark_many([j.slug for j in jobs])
                bootstrapped = True
                await bot.send_message(
                    chat_id=settings.telegram_chat_id,
                    text=(
                        f"Workana alerts active ✅\n"
                        f"Tracking {len(jobs)} jobs on first run (no spam).\n"
                        f"Checking every {settings.poll_interval_seconds}s."
                    ),
                )
                log.info("Bootstrap complete — watching for new jobs")
            else:
                new_jobs: list[WorkanaJob] = []
                for job in jobs:
                    if store.contains(job.slug):
                        continue
                    if not job.matches_keywords(settings.keywords):
                        store.add(job.slug)
                        continue
                    if not job.matches_max_bids(settings.max_bids):
                        store.add(job.slug)
                        continue
                    new_jobs.append(job)

                for job in reversed(new_jobs):
                    try:
                        try:
                            job = await enrich_job_from_detail(job)
                        except Exception as exc:
                            log.warning(
                                "Could not load client info for %s: %s",
                                job.slug,
                                exc,
                            )
                        await send_alert(bot, settings.telegram_chat_id, job)
                        log.info("Alert sent: %s", job.slug)
                    except TelegramError as exc:
                        log.error("Telegram error for %s: %s", job.slug, exc)
                    store.add(job.slug)
                store.save()

        except Exception as exc:
            log.exception("Poll failed: %s", exc)

        await asyncio.sleep(settings.poll_interval_seconds)


def main() -> None:
    try:
        settings = Settings.from_env()
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc

    asyncio.run(poll_loop(settings))


if __name__ == "__main__":
    main()
