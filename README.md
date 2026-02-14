# Threads Posting Backend

A Python-based backend for scheduling and publishing posts to Meta Threads, with content generation, scheduling, analytics, and REST APIs.

## Features
- FastAPI endpoints for queueing, publishing, and analytics.
- SQLAlchemy models for posts and metrics (SQLite by default).
- APScheduler for scheduling posts up to 365 days in advance.
- Threads API client with retries and error handling.
- OpenAI-backed content generator with low-cost text-first defaults.

## Meta Threads Setup
1. Create a Meta developer app at <https://developers.facebook.com/>.
2. Enable the Threads API and request **threads_basic** and **threads_content_publish** permissions.
3. Generate a user access token and capture:
   - `THREADS_ACCESS_TOKEN`
   - `THREADS_APP_ID`
   - `THREADS_APP_SECRET`

## Environment Configuration
Create a `.env` file:

```bash
THREADS_ACCESS_TOKEN=your_token
THREADS_APP_ID=your_app_id
THREADS_APP_SECRET=your_app_secret
OPENAI_API_KEY=your_openai_key
DEFAULT_TIMEZONE=UTC
DATABASE_URL=sqlite:///./threads.db
SCHEDULER_TIMEZONE=UTC
ALLOW_LLM_FOR_QUEUE=false
PREDEFINED_POSTS_CSV=examples/predefined_posts.csv
THREADS_DEFAULT_USER_ID=me
THREADS_DEFAULT_COUNTRY_CODE=US
THREADS_CAP_REPLY_TO_REPLY_IDS=false
THREADS_CAP_MANAGE_REPLY=false
THREADS_CAP_KEYWORD_SEARCH=false
THREADS_CAP_TRENDING_TOPICS=false
THREADS_CAP_FOLLOWER_DEMOGRAPHICS=false
OPS_CAPABILITY_PROBE_ENABLED=true
OPS_CAPABILITY_PROBE_INTERVAL_HOURS=24
OPS_CAPABILITY_PROBE_TIMEOUT_SECONDS=420
OPS_CAPABILITY_PROBE_OUTPUT_DIR=notes/strategy
OPS_CAPABILITY_PROBE_READ_ONLY=true
OPS_CAPABILITY_PROBE_AUTO_SYNC=true
OPS_CAPABILITY_PROMOTE_PASSES=2
OPS_CAPABILITY_DEMOTE_FAILS=1
OPS_KPI_SNAPSHOT_ENABLED=true
OPS_KPI_SNAPSHOT_INTERVAL_HOURS=24
OPS_KPI_SNAPSHOT_LOOKBACK_DAYS=14
OPS_KPI_SNAPSHOT_REFRESH_RECENT=true
OPS_KPI_SNAPSHOT_REFRESH_LIMIT=20
OPS_KPI_SNAPSHOT_OUT=notes/strategy/learning_profile_snapshots.jsonl
OPS_AUTO_LOG_PATH=notes/strategy/auto_ops_log.jsonl
PREDEFINED_POSTS_REPLENISH_ENABLED=true
PREDEFINED_POSTS_REPLENISH_DAYS=3
PREDEFINED_POSTS_REPLENISH_PER_DAY=4
PREDEFINED_POSTS_REPLENISH_INTERVAL_HOURS=12
PREDEFINED_POSTS_TINYURL_RATIO=0
TRENDING_REPLY_ENABLED=true
TRENDING_REPLY_INTERVAL_HOURS=1
TRENDING_REPLY_PER_HOUR=4
TRENDING_REPLY_BATCH_SIZE=1
TRENDING_REPLY_TOPICS_LIMIT=5
TRENDING_REPLY_SEARCH_LIMIT=10
TRENDING_REPLY_SEARCH_TYPE=RECENT
TRENDING_REPLY_KEYWORD_SEARCH_ENABLED=false
TRENDING_REPLY_KEYWORDS=ai tools,ai workflow,automation,creator workflow,inbox overload,meetings
AUTO_ENGAGE_REPLY_ENABLED=true
AUTO_ENGAGE_REPLY_INTERVAL_MINUTES=15
AUTO_ENGAGE_REPLY_PER_HOUR=8
AUTO_ENGAGE_REPLY_BATCH_SIZE=3
AUTO_ENGAGE_REPLY_LOOKBACK_DAYS=7
AUTO_ENGAGE_REPLY_POSTS_LIMIT=25
AUTO_ENGAGE_REPLY_SCAN_REPLIES_LIMIT=50
AUTO_ENGAGE_REPLY_SCAN_CONVERSATION_ENABLED=true
AUTO_ENGAGE_REPLY_MAX_COMMENT_AGE_HOURS=72
AUTO_ENGAGE_REPLY_ALLOW_DIRECT_FALLBACK=true
AUTO_ENGAGE_REPLY_REPLY_CONTROL=EVERYONE
AUTO_ENGAGE_REPLY_FALLBACK_TO_POST=true
AUTO_ENGAGE_REPLY_FALLBACK_POSTS_PER_RUN=1
THREADS_DEFAULT_REGION=US_EAST
THREADS_MIN_GAP_MINUTES=90
THREADS_DAILY_POST_CAP=4
THREADS_WEEKEND_POST_CAP=2
THREADS_SCHEDULE_LEAD_MINUTES=5
THREADS_SCHEDULE_LOOKAHEAD_DAYS=30
THREADS_SCHEDULE_SLOT_INTERVAL_MINUTES=15
THREADS_METRIC_LOOKBACK_DAYS=14
THREADS_METRIC_TOP_HOURS=3
CONTENT_OVERSAMPLE_FACTOR=2
CONTENT_REWRITE_BUDGET=6
CONTENT_NOVELTY_THRESHOLD=0.72
CONTENT_NICHE=ai_work_reality_check
CONTENT_LEARNING_LOOKBACK_DAYS=14
CONTENT_NOVELTY_LOOKBACK_DAYS=30
CONTENT_NOVELTY_CORPUS_LIMIT=400
QUEUE_LLM_MODEL=gpt-4.1-nano
QUEUE_LLM_GENERATE_IMAGES=false
```

### LLM usage

- `ALLOW_LLM_FOR_QUEUE` is off by default; the queue endpoint reads posts from `PREDEFINED_POSTS_CSV` and never hits OpenAI unless this opt-in flag is enabled temporarily for testing.
- Use `scripts/generate_predefined_schedule.py` with `OPENAI_API_KEY` to refresh `examples/predefined_posts.csv` via `gpt-4.1-nano` text-only batches. The replenisher runs once per batch, so the live queue continues to rely on the CSV and avoids per-post LLM charges.
- When `ALLOW_LLM_FOR_QUEUE=true`, queue generation uses `QUEUE_LLM_MODEL` (default `gpt-4.1-nano`) and stays text-only unless you explicitly set `QUEUE_LLM_GENERATE_IMAGES=true`.
- Queue-time LLM output and batch generation now pass through shared quality gates (length, placeholders, repetition, banned topics, novelty checks), with one budgeted rewrite pass before fallback copy.

### predefined_posts.csv format

- **topic**: high-level subject used to match queue requests.
- **tone**: descriptive tone (e.g., `playful`, `informal`) that narrows the pick.
- **text**: the body of the Threads post (keep it under 500 characters).
- **media_type**: `IMAGE` or `TEXT`; include a URL in `media_url` when `IMAGE`.
- **media_url**: publicly accessible image URL (optional for `TEXT` entries).
- **scheduled_time**: ISO 8601 timestamp (with timezone) that serves as the preferred posting time when none is provided in the API request.
- **target_regions**: optional semicolon/comma-separated list of region codes that the scheduler may use when picking a time.

### Validating the CSV

- Run `python scripts/validate_predefined_posts.py --fix` after editing `examples/predefined_posts.csv` to trim whitespace, normalize timestamps into ISO 8601 with timezone, remove accidental duplicate headers, and report any missing schedules or duplicates.  
- The script will rewrite the CSV when `--fix` is passed and prints a summary (rows processed, missing scheduled_time counts, duplicate keys) so you know what changed.  
- If the script cannot run (e.g., your Python interpreter is blocked), you can check the `notes/changelog.md` entry for the same validation logic as a reference before manually editing the CSV.

### Schedule density check

- Run `python scripts/analyze_schedule.py` whenever you update the CSV to see how many posts are scheduled per date/hour and where duplicates exist; it warns when more than four posts share a single day or when multiple entries share the same hour.  
- If the report highlights dense days or duplicates, spread the `scheduled_time` values apart (e.g., move some to other UTC dates or hours via ISO timestamps) so the `/queue` flow does not spam Threads.  
- The same script can be used regularly to keep the schedule well distributed before enabling automation, and its warnings reference line numbers so you can edit the CSV accordingly.

### Auto-populating the queue

- Keep `OPENAI_API_KEY` set when you want the system to regenerate several days of posts automatically. Run `python scripts/generate_predefined_schedule.py --days 3 --per-day 4` (or another combination as long as `days × per-day` stays divisible by 3) to produce batches that evenly span EU, US_EAST, and US_WEST. Each batch is blocked into region groups so the scheduler can place every post inside its tailored time window.  
- The generator now feeds the LLM a richer prompt that spells out the goal (fun, inspiring, slightly off-the-wall Threads-native copy), the hard constraints (text-only, no emojis or hashtags, 1–3 short sentences, no marketing or CTAs, no rage bait/politics, avoid clichés, and at least half end with a question or implicit prompt), and the required JSON schema with a single-element `target_regions` array so downstream logic knows where to route each piece.  
- Once the batch is accepted, the scheduler verifies the full headcount, splits the posts into EU/US_EAST/US_WEST groups, and selects times within the regional windows (EU: 07:30–09:00 or 18:00–20:00 UTC; US_EAST: 12:00–14:00 or 22:00–00:00 UTC; US_WEST: 15:00–17:00 or 01:00–03:00 UTC), keeping the slots naturally distributed.  
- Generation now runs an ever-learning loop: it reads recent post metrics, adapts commentary/whimsical mix and tone hints, oversamples candidates, filters low-quality or near-duplicate drafts, and rewrites a limited number using `gpt-4.1-nano` before scheduling.  
- When recent average views are low, the loop auto-tightens generation settings (higher oversampling + stricter novelty) even if you do not manually update `.env`.  
- Tune `CONTENT_OVERSAMPLE_FACTOR`, `CONTENT_REWRITE_BUDGET`, `CONTENT_NOVELTY_THRESHOLD`, `CONTENT_LEARNING_LOOKBACK_DAYS`, and `CONTENT_NOVELTY_*` env vars to control strictness and cost.  
- The generator alternates windows per region per day and enforces a 30-minute minimum gap between posts in the same region to prevent stacking.  
- The app registers a background replenishment job (`threads_poster.services.predefined_posts_replenishment`) that runs every 12 hours by default (adjustable via `PREDEFINED_POSTS_REPLENISH_INTERVAL_HOURS`) so the replenisher keeps adding new batches (controlled by `PREDEFINED_POSTS_REPLENISH_DAYS`/`PREDEFINED_POSTS_REPLENISH_PER_DAY`) as long as `PREDEFINED_POSTS_REPLENISH_ENABLED` remains `true`.  
- The generator writes `media_type=text` entries, randomizes minutes with the configured windows, and rewrites `examples/predefined_posts.csv` only when the requested batch is fully available (use `--force` to override the skip logic).  
- RSS ingest is now tuned for offbeat feeds (Oddity Central, Atlas Obscura, Boing Boing, IFLScience, ScienceAlert, PopSci), applies an "edge score" based on quirky keywords, and filters out war/politics/famine/religion before passing compact summaries to the LLM so posts stay light but still grounded.  
- The generator now targets a 1/3 commentary + 2/3 whimsical mix; no URLs are added by default to keep posts home-feed friendly. Set `PREDEFINED_POSTS_TINYURL_RATIO` above 0 if you ever want to re-enable link insertion.  
- The generation loop enforces specific question endings in most posts (default 70%, raised to 80% when reply rate is low) so conversation prompts are not left to chance.  
- Set `CONTENT_NICHE=ai_work_reality_check` to keep generated posts anchored to practical AI/work scenarios and avoid drifting into novelty topics.  
- To cap output at six posts per day, set `PREDEFINED_POSTS_REPLENISH_PER_DAY=6` (it remains divisible by 3, so you get 2 posts per region each day). Values above 6 are clamped.  
- Need a manual push? Run `python scripts/schedule_in_5.py`—it POSTs a quick test topic to the queue with a scheduled time five minutes from now so you can confirm the scheduler+publisher path works end-to-end without waiting for the replenisher job.

### Trending replies

- The scheduler also launches `threads_poster.services.trending_replier` (enabled via `TRENDING_REPLY_ENABLED`) as a discovery reply loop.  
- With keyword-search permissions enabled, it uses blended topic discovery (`TRENDING_REPLY_KEYWORDS` + RSS/news headline terms) to find recent public posts and reply.  
- With keyword-search disabled, it falls back to non-trending discovery by scanning active conversation roots from your own recent reply graph (`/me/replies` + `/me/threads`) and replying to unanswered comments there.  
- The job is rate-limited by the `TRENDING_REPLY_*` settings so it never posts more than four replies per hour; it tracks every reply in the database so you do not engage the same post twice.  
- You must request the `threads_keyword_search` and `threads_manage_replies` permissions for your Meta developer app so the service can discover public posts and post replies programmatically.  
- Keep `TRENDING_REPLY_KEYWORD_SEARCH_ENABLED=false` and `THREADS_CAP_KEYWORD_SEARCH=false` until those permissions are approved; the job will continue running in conversation-discovery mode.
- For detailed visibility, the app writes recognized events to dedicated log files under `logs/`:  
  - `logs/scheduled_posts.log` captures every scheduling/publishing attempt and outcome (`threads_poster.scheduled_posts`).  
  - `logs/replies.log` records every trending reply that was sent (`threads_poster.replies`).  
  - `logs/openai.log` records each OpenAI call made by `generate_predefined_schedule.py` with token usage and estimated cost (`threads_poster.openai`).  
  Tail the files (or use your own log viewer) to drill down into scheduled posts, reply calls, or GPT usage without parsing the main server log.
- Set `OPENAI_TOKEN_COST_PER_1K` (defaults to 0) to the per-1,000-token price you pay; each run logs `OPENAI_USAGE` entries in `logs/insights.log` with prompt/completion/total token counts plus an estimated cost so you can monitor spend locally. We now call `gpt-4.1-nano` (the budget-friendly option) for these batches.  
- Trigger the script again when the CSV runs low (automate it with your scheduler if desired); every time it finishes you can re-run the validator/analyzer to ensure the new data is normalized and still spread out.

### Auto Engagement Replies

- `threads_poster.services.auto_engagement_replier` runs on a schedule and replies to new comments on your own recent published posts.
- It uses the official reply-management publish path (`POST /me/threads` with `reply_to_id`, then `threads_publish`) and can fallback to the legacy `/{post-id}/replies` endpoint if enabled.
- It scans both direct replies and conversation threads, skips your own/hidden comments, avoids duplicate replies using `reply_log`, respects hourly caps, and asks a follow-up question to encourage thread depth.
- If `THREADS_CAP_REPLY_TO_REPLY_IDS=false`, automation replies to the parent post id (not the individual comment id) to match current API capability.
- If reply write permissions are missing, it automatically falls back to scheduling one follow-up post from comment themes (`AUTO_ENGAGE_REPLY_FALLBACK_*`) so engagement flow stays automated.
- Control cadence with `AUTO_ENGAGE_REPLY_*` env vars; defaults are production-safe for continuous automation.

### Token maintenance

- Once you complete the OAuth flow, the mkcert callback helper writes `THREADS_ACCESS_TOKEN`, `THREADS_ACCESS_TOKEN_EXPIRES_IN`, and `THREADS_TOKEN_OBTAINED_AT` to `.env`.
- The FastAPI scheduler now runs a job every 12 hours to refresh that token automatically via `GET https://graph.threads.net/refresh_access_token` whenever the stored expiry is within five days.
- The job overwrites the same `.env` entries and reloads the environment, so you only need to rerun the authorization flow when the refresh endpoint fails or you swap app credentials.

### Insights log

- A dedicated log at `logs/insights.log` records token refresh checks, refresh results, queued posts, and publish confirmations (`TOKEN_REFRESH_INITIATED`, `TOKEN_REFRESHED`, `POST_SCHEDULED`, `POST_PUBLISHED`).
- Tail (on Unix) or open that file to see timestamped entries with user IDs, regions, scheduled/published times, and spans between token grabs without digging into the database.
- Set `TOKEN_REFRESH_INTERVAL_HOURS` in `.env` (defaults to 24) if you want to lower the frequency of refresh checks; the job honors that value and keeps logging the verdicts so you can see when the shorter cadence actually triggers a refresh.
- The scheduler now runs `catch_up_scheduled_posts` every `CATCH_UP_INTERVAL_MINUTES` (default 30) to recover missed queued/scheduled posts whose `scheduled_time` slipped into the past. It spaces recovered posts by `CATCH_UP_STAGGER_MINUTES` (default 3) and stops when it reaches the next already-future post so normal cadence can resume. It logs `CATCHUP_SCHEDULED`/`CATCHUP_SKIPPED` events so you can see which posts were recovered. Posts default to publishing as `me` (the account tied to the access token) unless you override `THREADS_DEFAULT_USER_ID` or pass a different `user_id` when queueing.
- Tune `CATCH_UP_BATCH_SIZE` (default 6) and `CATCH_UP_STAGGER_MINUTES` (default 3) in `.env` to adjust how many stale posts are recovered per run and how far apart their new run times land. Keeping the batch small avoids dumping everything at once.
- The trending reply job (if enabled) now sends `country_code` with every `trending_topics` call and falls back to `THREADS_DEFAULT_COUNTRY_CODE` (default `US`), so make sure that env var matches the region you want to sample. If the API still rejects the request we log the upstream error without crashing.

## Platform Setup

### Ubuntu (bash)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Start the API + scheduler:

```bash
./scripts/run_api.sh
```

### Windows (PowerShell)

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Start the API + scheduler:

```powershell
scripts\run_api.bat
```

## Database Initialization
Create tables automatically on app startup or run manually:

```bash
python -c "from threads_poster.db.init_db import init_db; init_db()"
```

## Running the API + Scheduler

```bash
uvicorn threads_poster.main:app --reload
```

The scheduler starts with the FastAPI app.

## API Usage

### Queue a post

```bash
curl -X POST http://localhost:8000/queue \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "Threads best practices",
    "tone": "insightful",
    "target_regions": ["UK", "US"],
    "user_id": "me"
  }'
```

### Publish immediately

```bash
curl -X POST "http://localhost:8000/posts/{post_id}/publish?user_id=me"
```

### Reschedule future predefined posts

```bash
curl -X POST http://localhost:8000/reschedule \
  -H "Content-Type: application/json" \
  -d '{"source":"predefined","min_gap_minutes":30,"dry_run":false}'
```

### Fetch a post with metrics

```bash
curl http://localhost:8000/posts/{post_id}
```

### Aggregate insights

```bash
curl "http://localhost:8000/insights?metric_name=views&group_by=week"
```

### Refresh follower demographics (UK/US breakdown)

```bash
curl "http://localhost:8000/insights?refresh_demographics=true&user_id=me"
```

### Content Learning Profile (with optional live post-insights refresh)

```bash
curl "http://localhost:8000/content/learning-profile?lookback_days=14&refresh_recent=true&refresh_limit=20&user_id=me"
```

- Returns a performance summary (`avg_views`, `median_views`, `reply_rate`, etc.), best UTC hours, top/weak tones, low-performing posts, and recommended env overrides.
- Set `refresh_recent=false` to read cached metrics only.

### Trigger Auto-Engagement Reply Run (manual)

```bash
curl -X POST http://localhost:8000/ops/auto-engage/run-now
```

- Runs the auto-engagement reply loop immediately (outside its normal interval cadence).
- Check `logs/insights.log` for `AUTO_ENGAGE_REPLY_RUN` and `logs/replies.log` for reply/fallback entries.

### Trigger Capability Probe (manual)

```bash
curl -X POST http://localhost:8000/ops/capability-probe/run-now
```

- Runs the capability probe immediately and writes/updates verification reports in `notes/strategy/`.
- Honors `OPS_CAPABILITY_PROBE_READ_ONLY`; when `true`, the probe does not publish posts or replies.

### Trigger KPI Snapshot (manual)

```bash
curl -X POST http://localhost:8000/ops/kpi-snapshot/run-now
```

- Runs the KPI snapshot loop immediately and appends to `notes/strategy/learning_profile_snapshots.jsonl`.

### Threads API Verification Runner

Run a full permission-path verification sweep and write a JSON report:

```bash
python scripts/threads_api_verification.py
```

- Output report: `notes/strategy/threads_api_verification_<timestamp>.json`
- Exit code `0` means all required calls passed.
- Required/optional behavior follows `THREADS_CAP_*` env flags by default.
- Optional flags:
  - `--require-keyword-search`
  - `--require-trending-topics`
  - `--skip-manage-reply`
  - `--require-manage-reply`
  - `--require-reply-to-reply`
  - `--skip-reply-to-reply`
  - `--read-only`

### Operations Automation

- `threads_poster.services.ops_automation` runs two scheduler jobs automatically:
  - capability probe + safe `THREADS_CAP_*` auto-sync
  - KPI snapshot + delta logging
- Set `OPS_CAPABILITY_PROBE_READ_ONLY=true` (recommended) to keep scheduled probes non-mutating and timeline-invisible.
- Duplicate-run protection is enabled: scheduled jobs skip when a same-cycle run was already recorded recently (for example after restarts).
- Manual endpoints force execution even when duplicate-run protection would skip.
- Capability auto-sync rule:
  - promote capability to `true` after `OPS_CAPABILITY_PROMOTE_PASSES` consecutive successful probes
  - demote capability to `false` after `OPS_CAPABILITY_DEMOTE_FAILS` consecutive failures
- Operational outputs:
  - verification reports: `notes/strategy/threads_api_verification_*.json`
  - KPI snapshots: `notes/strategy/learning_profile_snapshots.jsonl`
  - auto-ops events: `notes/strategy/auto_ops_log.jsonl`

## Scheduling Logic
- Queue auto-scheduling is region-aware (`EU`, `US_EAST`, `US_WEST`) and uses UTC posting windows tuned for Threads home-feed visibility.
- The scheduler enforces a minimum gap between queued posts (`THREADS_MIN_GAP_MINUTES`, default 90) and daily caps (`THREADS_DAILY_POST_CAP`, default 4; weekend default 2) to prevent over-posting.
- If historical `views` metrics exist, slot selection prefers the strongest recent UTC hours (lookback/tuning via `THREADS_METRIC_LOOKBACK_DAYS` and `THREADS_METRIC_TOP_HOURS`).
- If predefined CSV rows have missing or stale `scheduled_time`, they are automatically re-slotted instead of being dropped.

## Seed a Year of Posts
Use the seed script to queue a batch of posts:

```bash
python scripts/seed_schedule.py examples/seed_posts.csv http://localhost:8000
```

Example files live in `examples/seed_posts.csv` and `examples/seed_posts.json`.

## Project Structure
```
threads_poster/
  api/
  clients/
  core/
  db/
  services/
  main.py
scripts/
examples/
```

## Extending
The architecture is modular; add new endpoints or features (polls, spoilers, location tagging) by extending the API routes and the `ThreadsClient` methods.
