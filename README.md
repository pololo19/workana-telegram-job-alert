# Workana → Telegram job alerts

Polls [Workana](https://www.workana.com/jobs) job listings and sends Telegram messages when new projects appear that match your filters.

Workana has **no public API**, so this bot reads the same public listing page your browser uses and extracts embedded job data (no login required).

## Quick start

1. **Telegram bot + channel**
   - Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
   - Create a **Telegram channel** for job alerts.
   - Channel → **Administrators** → add your bot → turn on **Post messages**.
   - Post any message in the channel (so Telegram registers the bot there).
   - Run `python get_chat_id.py` and copy the line for your channel into `.env` as `TELEGRAM_CHAT_ID` (e.g. `@my_workana_jobs` or `-1001234567890`).

   The bot only **pushes** alerts into the channel; it does not reply in a private bot chat. To use your own DM instead of a channel, set `TELEGRAM_CHAT_ID` to your user id from [@userinfobot](https://t.me/userinfobot).

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

## Ubuntu VPS

Do **not** copy the Windows `.venv` folder to the server. Upload the project (git clone, `scp`, etc.) and create a fresh venv on Linux.

1. **Packages**

   ```bash
   sudo apt update
   sudo apt install -y python3 python3-venv python3-pip curl
   ```

2. **App directory** (example)

   ```bash
   cd ~
   git clone <your-repo-url> workana-telegram-bot   # or upload the folder
   cd workana-telegram-bot
   cp .env.example .env
   nano .env   # token, TELEGRAM_CHAT_ID, WORKANA_JOBS_URL, filters
   ```

3. **Install & test**

   ```bash
   chmod +x run.sh
   ./run.sh
   ```

   Stop with `Ctrl+C` once you see polling logs and (if configured) a test alert.

4. **Run as a service** (survives reboot)

   Create `/etc/systemd/system/workana-bot.service` (replace `YOUR_USER` and paths):

   ```ini
   [Unit]
   Description=Workana Telegram job alerts
   After=network-online.target
   Wants=network-online.target

   [Service]
   Type=simple
   User=YOUR_USER
   WorkingDirectory=/home/YOUR_USER/workana-telegram-bot
   ExecStart=/home/YOUR_USER/workana-telegram-bot/.venv/bin/python bot.py
   Restart=on-failure
   RestartSec=15
   Environment=PYTHONUNBUFFERED=1

   [Install]
   WantedBy=multi-user.target
   ```

   First-time venv (if you have not run `./run.sh` yet):

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

   Enable:

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now workana-bot
   sudo systemctl status workana-bot
   journalctl -u workana-bot -f
   ```

5. **VPS notes**

   - Install **curl** (used when Workana returns 403/429).
   - Datacenter IPs sometimes hit **Cloudflare** more often than a home connection. If logs show “Cloudflare challenge”, raise `POLL_INTERVAL_SECONDS` (e.g. 120–180) or try another region/provider.
   - Persisted state lives in `data/seen_jobs.json` — back it up if you rebuild the VPS so you do not get duplicate “new job” floods.

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
