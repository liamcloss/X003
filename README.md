# Threads Posting Backend

A Python-based backend for scheduling and publishing posts to Meta Threads, with content generation, scheduling, analytics, and REST APIs.

## Features
- FastAPI endpoints for queueing, publishing, and analytics.
- SQLAlchemy models for posts and metrics (SQLite by default).
- APScheduler for scheduling posts up to 365 days in advance.
- Threads API client with retries and error handling.
- OpenAI-backed content generator with image-first strategy.

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
```

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

## Scheduling Logic
- Posts are scheduled between **07:00 and 09:00 local time** on weekdays.
- **Wednesday** is prioritized; Thursday and Friday follow.
- Sundays are avoided unless explicitly scheduled.

## Seed a Year of Posts
Use the seed script to queue a batch of posts:

```bash
python scripts/seed_schedule.py examples/seed_posts.csv http://localhost:8000
```

Example files live in `examples/seed_posts.csv`, `examples/seed_posts.json`, and `examples/seed_posts_existing.csv`.

### CSV schema
The seed script supports two modes in the same CSV: generate content from a topic **or** ingest prewritten posts.

| Column | Required | Description |
| --- | --- | --- |
| `topic` | Required if `text` is empty | Topic used by the content generator. |
| `text` | Required if `topic` is empty | Prewritten post text to schedule directly. |
| `tone` | Optional | Tone for generated posts (defaults to `neutral`). |
| `media_type` | Optional | Use `TEXT` or `IMAGE` when providing `text`. |
| `media_url` | Optional | Image URL for `IMAGE` posts. |
| `target_regions` | Optional | JSON array string such as `["UK", "US"]`. |
| `scheduled_date` | Optional | ISO-8601 timestamp. If blank, the scheduler picks the next optimal slot. |
| `user_id` | Optional | Threads user id (defaults to `me`). |

When you include `text`, the API skips generation and schedules the post exactly as provided.

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
