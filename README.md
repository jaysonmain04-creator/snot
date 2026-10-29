# Snot — your Discord trading journal reader

Owner-only bot that reads journal text and chart screenshots across the channels it can access. `/read_history` reviews the selected journal scope in batches, including image-only posts. `/ask` searches the resulting text and cached image observations for follow-up questions. This is a separate OpenAI-powered bot, not your ChatGPT conversation.

## Commands

| Command | What it does |
|---|---|
| `/export` | Export live messages and original image files; optional start_date/end_date (UTC, inclusive end). No OpenAI calls. |
| `/export_status` | Background export progress and number of ZIP parts. |
| `/export_download part:1` | Download one ZIP part privately; repeat for every part and upload them to ChatGPT. |
| `/read_history` | Refresh accessible history, analyze uncached images, and produce a journal review. Optional question, start_date, and end_date. No dates means all imported history. |
| `/status` | Import progress, image-analysis counts, and review progress. |
| `/report` | Download the latest complete or partial review privately. |
| `/cancel_review` | Stop a long review; retain completed image notes. An API request already sent may still incur charges. |
| `/ask question:...` | Quick keyword search across text and analyzed image notes, up to 80 matches. |
| `/summary` | Quick summary of the latest 120 messages and any cached image notes. |

Example: `/read_history question:What setups did I plan, what actually happened, and what mistakes repeat? start_date:2026-10-01 end_date:2026-10-28`

Dates use UTC; end_date includes that entire day. Commands require the current server owner and reply ephemerally. A review can take longer than Discord's response window; check `/status` and retrieve it with `/report`. Ordinary mentions and natural-language messages do not trigger commands. The process runs in the background on your host, not inside ChatGPT.

## Deploy on Railway

1. Connect this GitHub repository to a Railway service using **New Project → Deploy from GitHub repo**. Select the branch containing these files. Dockerfile must be at its root.
2. In the service's **Variables**, set:

   | Variable | Value |
   |---|---|
   | `DISCORD_BOT_TOKEN` | Your Discord bot token |
   | `OPENAI_API_KEY` | Your OpenAI API key |
   | `DISCORD_GUILD_ID` | Your Discord server ID |
   | `DATA_DIR` | `/data` |
   | `OPENAI_MODEL` | Optional; defaults to `gpt-4.1-mini` |
   | `VISION_MODEL` | Optional image-capable model; defaults to OPENAI_MODEL |

3. Attach a persistent **Volume mounted at `/data`**. Keep **one replica** and disable sleeping/serverless. No public domain is needed.
4. Deploy. Initial startup without required variables will fail until they are added. Enable paid hosting and OpenAI API billing as needed; ChatGPT Plus does not cover API charges.
5. Run `/status` in Discord, then `/read_history`. For the first live check, use a small date range with a chart screenshot and verify the source link and image count before reviewing the entire journal.

In Discord Developer Portal, enable **Message Content Intent**. Install the app with Guild Install, scopes `bot` and `applications.commands`, and permissions **View Channels, Read Message History, Send Messages, Attach Files**. Channel overrides still apply; Administrator is unnecessary. Enable Discord Settings → Advanced → Developer Mode, then right-click the server icon → Copy Server ID.

Never commit API keys, bot tokens, downloaded journal images, or the database. Store secrets in Railway Variables. The repository can contain public source code without exposing the private journal; only your running host stores that data. Tell any other journal contributors how the bot processes their messages.

## Images and review coverage

- Stores image metadata even when the post has no text. Supports uploaded PNG/JPEG/WebP/still GIF images and Discord-proxied embed images/thumbnails, including accessible forum threads.
- Uses real `input_image` vision requests at high detail. It reads symbols, visible chart labels, marked entries/stops/targets, annotations, and associated written plans. Prompts distinguish visible evidence, your written intent, uncertain readings, and documented outcomes; drawn arrows alone are not treated as executed trades.
- Re-fetches messages to refresh expiring Discord image URLs before downloading. Restricts downloads to Discord's media hosts, disallows redirects, and enforces 12 MiB and 25-megapixel limits. Images are normalized in memory to PNG with a maximum dimension of 2048 pixels; fine print can remain unreadable. Animated images are reported as unsupported, rather than silently reading one frame.
- Saves image notes in SQLite for reuse. Later `/ask` searches include those notes. An edited message caption or replaced attachment invalidates relevant notes. Temporary image bytes are not persisted to disk.
- `/read_history` sends every in-scope stored message and available image observation through bounded review batches, then combines the notes. Reports include coverage counts, skipped-image links, original message references, and detailed batch notes. Summarization is lossy; it is not a guarantee that every detail survives into the final findings.
- Full reviews refresh old messages and reconcile deletions for successfully scanned channels. Routine ten-minute imports catch new messages; online edits/deletions update the archive. Interrupted/failed scans can leave stale text; the report includes import failures. Inaccessible/unjoined private threads, deleted messages, voice/video, unsupported files, and external images without a Discord proxy cannot be reviewed.
- Image-analysis failures are reported; API failures stop the job. Completed image notes survive restarts. A running review is marked interrupted after a restart: rerun `/read_history` to reuse its cached images. Review summaries are regenerated, so they may incur charges again.
- `/ask` and `/summary` are bounded shortcuts, not exhaustive reviews. Run `/read_history` first to analyze images. Model descriptions can misread charts; verify exact levels and outcomes against the linked originals. No trading execution or broker connection is included.

## Costs and data

Routine message/metadata imports do not call OpenAI. `/read_history` uses approximately one vision call per uncached image plus text-review and synthesis calls, with no hidden image-count cap. A large first review can therefore cost more and take much longer than a quick question. Use a small date range first and provider account spend controls. Requests use `store=False`; this does not promise zero provider retention. Captions, journal text, image metadata, and reports remain in your Railway volume. Changes to channel permissions do not automatically erase stored data or previously generated reports; reports remain available only to the server owner. Remove the volume/database to delete the retained archive.

## Development and verification

Python 3.12; `pip install -r requirements.txt`, then `python -m unittest discover -s tests -v`.

Offline tests cover image-only posts, cache reuse and invalidation, genuine vision payload construction, failed-image coverage, migration/restart state, source preservation during batching, owner access, search permissions, and deletion cleanup. Actual Discord/API calls and Railway deployment must be verified using the owner's accounts; tests use mocks and no credentials.

API references: [OpenAI vision](https://developers.openai.com/api/docs/guides/images-vision), [discord.py](https://discordpy.readthedocs.io/en/stable/), [Discord CDN URLs](https://discord.com/developers/docs/reference#signed-attachment-cdn-urls), [Railway services](https://docs.railway.com/services).


## Export images and messages to ChatGPT

Run `/export start_date:2026-10-01 end_date:2026-10-28`, check `/export_status`, then run `/export_download part:1`, `part:2`, etc. No dates exports all available history. Download every part and upload them in your ChatGPT conversation. This does not automatically send anything to ChatGPT or call OpenAI.

The export reads live history in discovered accessible text channels and active/archived threads. Each message JSON includes original text, author/name ID, timestamp, channel, Discord link, reply ID, attachment metadata, and image paths/part names. Reply targets outside the range are not fetched separately. ZIPs contain original downloaded PNG/JPEG/WebP/GIF bytes without resizing, including supported Discord-proxied embed images. Video and other attachments are metadata only. External images without a supported Discord proxy, inaccessible threads and deleted messages cannot be downloaded.

Exports persist under `DATA_DIR/export`; only the latest export is kept and a new export replaces it. No journal files are committed to GitHub. Downloads require the current server owner and recheck channel access. ZIP parts are capped at 7 MB (or the server upload limit minus overhead). A single image larger than the part limit is explicitly skipped; see each message's image errors and the final manifest for coverage. Total output is capped at 1 GiB; if it fails, choose a smaller date range. Incomplete exports cannot be downloaded. A restart interrupts a running export; rerun it. Completed exports survive restarts on a persistent volume.

## Trading-journal review focus

`/read_history` now defaults to recurring terminology, premarket scenarios, intraday observations, documented entry reasons and confluence, recorded outcomes, and repeated habits. It asks for source evidence and counterexamples and separates planned trades from actual executions. The report also includes deterministic repeated-word/abbreviation counts from written messages (each word counts once per message), with original source links. These counts exclude model image descriptions; chart text is handled by the vision evidence review and is not represented as an exact OCR frequency count. Counts are across the imported human journal posts, not only the owner's posts if others contribute.

For independent checking in ChatGPT, upload `/report` together with all `/export_download` ZIP parts for the same date range. Images can be unreadable or unsupported; check the coverage and error lists. `/ask` and `/summary` remain bounded shortcuts; use `/read_history` without dates for the full accessible imported journal.
