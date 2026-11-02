# Workana → Telegram job alerts

Polls [Workana](https://www.workana.com/jobs) job listings and sends Telegram messages when new projects appear that match your filters.

Workana has **no public API**, so this bot reads the same public listing page your browser uses and extracts embedded job data (no login required).

## Quick start

1. **Telegram bot**
   - Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
   - Start a chat with your bot and send any message.
   - Get your chat ID from [@userinfobot](https://t.me/userinfobot) (or similar).

2. **Configure**

   ```powershell
   cd "F:\Auto RAM Cleaner\workana-telegram-bot"
   copy .env.example .env
   # Edit .env with your token, chat ID, and filters
   ```

3. **Install & run**

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   python bot.py
   ```

4. **Keep it running** (optional): use Task Scheduler, `nssm`, or a small VPS so alerts continue when your PC is off.

## Filters

| Setting | Purpose |
|--------|---------|
| `WORKANA_JOBS_URL` | Full URL from Workana after you set category, skills, language, etc. Always include `order=recent`. |
| `KEYWORDS` | Comma-separated; job must match **one** keyword in title, description, or skills. Empty = all jobs from URL. |
| `MAX_BIDS` | e.g. `5` or `0` (zero bids only). Empty = ignore bid count. |
| `POLL_INTERVAL_SECONDS` | Default `90`. Minimum `30`. Lower = faster alerts, more requests to Workana. |

### Example URLs

- All languages, newest first:  
  `https://www.workana.com/en/jobs?order=recent&language=xx`
- IT & programming:  
  `https://www.workana.com/en/jobs?category=it-programming&order=recent&language=xx`
- Python skill filter:  
  `https://www.workana.com/en/jobs?skills=python&order=recent&language=xx`

Open filters in the browser, copy the address bar, paste into `.env`.

## How it works

1. On **first run**, it records all jobs currently on page 1 and sends one “alerts active” message (no flood of old jobs).
2. Every poll, it compares slugs against `data/seen_jobs.json` and notifies on **new** matches only.

## Cloudflare

If you see “Cloudflare challenge” in logs, Workana is rate-limiting or blocking your IP. Increase `POLL_INTERVAL_SECONDS` or run from a normal home connection.

## Legal / etiquette

Use reasonable poll intervals. Respect [Workana’s terms](https://www.workana.com) and `robots.txt`. This tool is for personal freelance alerts, not bulk scraping.
