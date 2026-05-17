# Aura Backend

FastAPI backend for the Aura personal growth assistant. Designed for free-tier deployment on Render.com.

## Quick Start

```bash
cd projects/backend
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials (see below)
python main.py
```

Server runs at `http://localhost:8000`. API docs at `http://localhost:8000/docs`.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check / Render keep-alive ping |
| GET | `/api/calendar/sync?date=YYYY-MM-DD` | Fetch Google Calendar events |
| GET | `/api/schedule/optimize?date=YYYY-MM-DD` | Run fluid rescheduler |
| POST | `/api/chat` | Chat with Gemini 1.5 Flash |
| GET | `/api/habits/summary` | Habit streaks from Supabase |

---

## Configuration

### 1. Google Gemini API Key (Free Tier)

1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Sign in with a Google account
3. Click **Create API key** — select a free-tier project (or create one)
4. Copy the key
5. Paste as `GEMINI_API_KEY` in `.env`

**Free tier limits:** 1,500 requests/day on Gemini 1.5 Flash. More than enough for personal use.

### 2. Supabase (Free Tier Postgres)

1. Go to [supabase.com](https://supabase.com) and create a free project
2. Wait 1–2 minutes for the database to provision
3. Go to **Project Settings → API**
4. Copy the **Project URL** and **anon public** key
5. Paste as `SUPABASE_URL` and `SUPABASE_KEY` in `.env`

**Required tables** (create via SQL Editor in Supabase dashboard):

```sql
-- Fluid tasks (habits that can be rescheduled)
CREATE TABLE fluid_tasks (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
    priority INTEGER DEFAULT 1 CHECK (priority >= 1 AND priority <= 10),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Habit tracking
CREATE TABLE habits (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    streak INTEGER DEFAULT 0,
    last_completed TIMESTAMPTZ,
    completion_rate_7d REAL DEFAULT 0.0 CHECK (completion_rate_7d >= 0 AND completion_rate_7d <= 1),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Brain vault (voice transcripts, ideas, expenses)
CREATE TABLE brain_vault (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    transcript TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed some default fluid tasks
INSERT INTO fluid_tasks (name, duration_minutes, priority) VALUES
    ('Morning Workout', 45, 9),
    ('Breakfast', 20, 8),
    ('Lunch', 30, 8),
    ('Dinner', 30, 7),
    ('Evening Walk', 30, 5),
    ('Sleep Wind-Down', 45, 10);
```

### 3. Google Calendar API (OAuth 2.0)

1. Go to [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. Create a new project (or use existing)
3. Search for **Google Calendar API** and click **Enable**
4. Go to **Credentials → Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Name: `Aura Backend`
5. Copy the **Client ID** and **Client Secret**
6. Generate a refresh token using the [OAuth 2.0 Playground](https://developers.google.com/oauthplayground/):
   - Click the gear icon (⚙️) → check **Use your own OAuth credentials**
   - Enter your Client ID and Client Secret
   - In Step 1, select **Google Calendar API v3** → `https://www.googleapis.com/auth/calendar.readonly`
   - Click **Authorize APIs** → sign in → **Allow**
   - In Step 2, click **Exchange authorization code for tokens**
   - Copy the **Refresh token**
7. Paste all three values as `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REFRESH_TOKEN` in `.env`

---

## Architecture

```
projects/backend/
├── main.py                    # FastAPI app, routes, lifespan
├── scheduler.py               # Pure algorithm — timeline shifting
├── requirements.txt           # Pinned dependencies
├── .env.example               # Environment variable template
├── config/
│   └── settings.py            # pydantic-settings singleton
├── models/
│   ├── __init__.py
│   └── schemas.py             # Pydantic request/response models
├── services/
│   ├── __init__.py
│   ├── calendar_service.py    # Google Calendar OAuth + fetching
│   ├── gemini_service.py      # Gemini 1.5 Flash client
│   └── supabase_service.py    # Supabase async wrapper
└── cron/
    ├── __init__.py
    └── proactive_engine.py    # Hourly background worker
```

### How the Scheduler Works

The `scheduler.py` module is a **pure function** — no I/O, no side effects.

1. **Classify:** Calendar events are tagged as `fixed` (exams, classes, meetings) or `fluid` (workouts, meals)
2. **Extract gaps:** Walk through sorted fixed events, compute time windows between them
3. **Timeline shift:** If a fixed event ran late (`max(cursor, event.end)`), subsequent gaps shrink automatically
4. **Greedy placement:** Fluid tasks are placed by priority (highest first) into the first gap large enough
5. **Return:** Composite schedule with placed items + unplaced tasks (with reasons)

Overlap is **structurally impossible** — gaps are derived from fixed boundaries, and fluid tasks shrink gaps from the front.

### Proactive Engine

The background worker runs every hour as an `asyncio.Task` in FastAPI's lifespan:

1. Fetches today's Google Calendar events
2. Formats them into a prompt with whitespace gap analysis
3. Sends to Gemini 1.5 Flash for proactive recommendations
4. Logs the recommendation to Supabase's `brain_vault` table

**Render.com keep-alive:** Set up a free cron at [cron-job.org](https://cron-job.org) to ping `GET /api/health` every 10 minutes. This prevents Render from spinning down the free instance.

---

## Deploying to Render.com

1. Push this repo to GitHub
2. In Render, create a new **Web Service**
3. Connect your GitHub repo
4. Settings:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python main.py`
   - **Root Directory:** `projects/backend`
5. Add all environment variables from `.env` in Render's dashboard
6. Set up [cron-job.org](https://cron-job.org) to ping `/api/health` every 10 minutes
