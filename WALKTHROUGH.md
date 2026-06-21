# Technical walkthrough

## 1. System flow

1. `run_daily.py` determines today's match date in `America/Chicago`.
2. `pipeline.run_for_date()` requests the selected World Cup date from football-data.org and directly
   normalizes stable IDs, teams, UTC kickoff, venue, stage, and group from its JSON response.
3. Three Tavily searches run concurrently for every match: team availability/form, betting markets,
   and tactical/venue context.
4. DeepSeek compresses the bounded snippets once into a Pydantic-validated evidence digest.
5. All configured analyst models run concurrently through OpenRouter and reuse that digest.
6. The master model receives the digest and every analyst opinion, then writes a calibrated synthesis.
7. Fixtures, raw evidence, digest, and analyses are upserted into SQLite.
8. `app.py` reads saved rows and displays analyst cards side by side, followed by the master answer.

The app does not scrape bookmakers directly. Tavily returns public web search evidence, which may be
stale or region-specific. Tavily's generated answers are disabled throughout; prompts require the configured
models to interpret retrieved content, label uncertainty, and avoid inventing odds.

## 2. Files and functions

### `config.py`

All settings live here. `load_dotenv()` reads a local `.env`; GitHub Actions supplies the same names as
environment variables. `MODELS` is the analyst roster. `MASTER_MODEL` defaults to GPT 5.5 but can be
overridden without editing code. Paths are anchored to the repository so commands work from any folder.

### `database.py`

- `connect(path)` is a context manager. It creates the parent directory, returns rows addressable by
  column name, commits successful work, and always closes the connection.
- `initialize_database(path)` creates `matches` and `analyses` plus a date index. The schema is small on
  purpose: provider payloads remain in JSON while searchable identity fields stay as SQL columns.
- `save_match(match, path)` performs an upsert keyed by the football-data.org match ID, so reruns update
  rather than duplicate.
- `save_analysis(...)` upserts research, panel answers, and final recommendation as JSON/text.
- `get_day(day, path)` uses a left join so newly discovered matches still appear before analysis succeeds.
  It converts stored JSON strings back into Python dictionaries.

SQLite is preferable to one JSON file per day because writes are transactional, date queries are indexed,
and the schema can later gain results, evaluation scores, or users. It still remains a portable single file.

### `services.py`

- `openrouter_chat(model, messages, temperature)` posts the OpenAI-compatible chat payload. Tenacity
  retries transient failures up to three times with exponential backoff.
- `get_fixtures_football_data(day)` converts the Central-time day boundaries to UTC, requests every provider date
  touched by that interval, filters with exact timestamps, and normalizes the remaining fixtures.
- Referee name, provider role, and nationality are copied into each normalized fixture and stored as JSON.
- `research_match(match)` launches three Tavily searches in a thread pool. Search is I/O-bound, so threads
  reduce wall-clock time without complicated async code. Queries include both teams, World Cup 2026, the
  Central match date, and the specific evidence category; social-media domains are excluded. `basic` depth
  costs one Tavily credit per search and returns one NLP page summary per URL.
- Query strings intentionally use compact entity-and-intent terms rather than conversational questions:
  team names and tournament identify the entity, while terms such as `predicted lineups` or `double chance`
  steer retrieval. Each query remains below Tavily's recommended 400-character limit.
- `include_answer=False` disables Tavily's separate LLM-generated answer. The response still contains ranked
  `results`, each with title, URL, relevance score, and `content`; those four bounded fields are retained as
  raw evidence for the summarizer and audit storage.
- Betting search names the markets the analysts must compare: 1X2, double chance, draw-no-bet, Asian
  handicap, totals 1.5/2.5/3.5, and both-teams-to-score. These terms steer semantic retrieval rather than
  guarantee that every result contains every market. Betting results are restricted to a curated domain
  list and checked again locally because odds are the most decision-sensitive evidence category.
- Result caps deliberately favor betting evidence: team news uses `4 x 1,200` characters, betting uses
  `5 x 1,600`, and tactical/venue/weather context uses `3 x 800`. These are worst-case ceilings; short
  Tavily summaries remain short. Across the three searches the theoretical maximum falls to 15,200
  content characters, compared with 27,000 under the earlier uniform limits.
- `analyst_prompt(match, research)` supplies the same evidence and output contract to every model. Requiring
  probabilities totaling 100% makes opinions easier to compare. All analyst output is Simplified Chinese.
- `run_analysts(models, prompt)` calls up to five models concurrently. Exceptions become visible output for
  that model; one provider failure does not cancel the panel.
- `master_prompt(...)` asks for synthesis rather than majority vote, explicitly permits `No bet`, and
  requires the final recommendation in Simplified Chinese.
- `summarize_research(match, research)` makes one inexpensive DeepSeek call using JSON mode, then validates
  field lengths, list sizes, and unknown fields with Pydantic. Analysts reuse the validated digest, while the
  original snippets remain in SQLite. If summarization fails, the bounded raw evidence is used for that run.

### `pipeline.py`

- `run_for_date(day)` owns the end-to-end sequence. It initializes storage, discovers fixtures,
  researches each match, creates the shared digest, gets panel opinions, calls the master at lower
  temperature, saves, and returns display-ready results. A configurable match ceiling limits accidental spend.

### `run_daily.py`

- `main()` parses `--date` and `--midnight-guard`. Without `--date`, it uses the Central-time date, not the
  runner's UTC date. The guard makes two UTC cron entries behave as one Chicago-midnight schedule across DST.

Examples:

```powershell
python run_daily.py
python run_daily.py --date 2026-06-20
```

### Date semantics

The user-selected date always means a North American Central calendar day:

1. `run_daily.py` computes today in `America/Chicago`. GitHub runs at both possible UTC equivalents of
   Chicago midnight, and `--midnight-guard` allows only the invocation whose Central hour is `00`.
2. The code builds a half-open local interval: `[Central 00:00, next Central 00:00)`.
3. `zoneinfo` converts both boundaries to UTC. In June, Chicago observes CDT, so June 20 becomes
   `[2026-06-20 05:00Z, 2026-06-21 05:00Z)`. In winter the UTC-6 offset is applied automatically.
4. football-data.org is queried for all UTC dates touched by that interval. Every `utcDate` is then filtered
   against the exact boundaries; the exclusive end prevents double counting.

Verification against the current 104-match schedule found Central start times from 11:00 through 23:00,
with no midnight starts. Thirty-six matches have different UTC and Central calendar dates, so matching only
the UTC date is not reliable. UTC and Central kickoff strings are both retained for display and audit.

### `app.py`

- `_date_string(value)` normalizes Gradio strings and Python datetimes to `YYYY-MM-DD`.
- `_choices(rows)` creates readable dropdown labels while retaining stable match keys as values.
- `load_date(value)` blocks future dates, queries SQLite, and updates dropdown/state components.
- `show_match(match_key, rows)` returns values in exactly the same order as the Gradio output components.
- `analyze_date(value)` validates the date, runs the pipeline, reloads storage, and converts failures into a
  visible Gradio error.
- `build_app()` constructs the themed Blocks interface and wires load, run, selection-change, and startup
  events. Five analyst cards are produced from `MODELS`, so labels stay synchronized with configuration.

### `.github/workflows/daily-analysis.yml`

GitHub cron uses UTC and does not understand DST. Chicago midnight is 05:00 UTC during CDT and 06:00 UTC
during CST, so the workflow starts at both `05:05` and `06:05` UTC. `--midnight-guard` exits from the wrong
one. Five minutes past the hour is intentional because scheduled jobs can be delayed near busy hour boundaries.

The workflow installs dependencies, reads repository secrets, runs analysis, and commits
`data/championship.db`. The `contents: write` permission is required. Branch protection may block bot pushes;
in that case use an artifact, release, or hosted database instead.

### Tests and supporting files

- `tests/test_database.py` proves a complete SQLite save/load round trip with an isolated temporary DB.
- `tests/test_app_helpers.py` checks UI normalization helpers without calling paid APIs.
- `.env.example` documents secrets and cost controls without exposing real values. `.env` is git-ignored.
- `requirements.txt` uses bounded major versions to reduce surprise breaking changes.
- `ai_world_cup_walkthrough.ipynb` teaches the implementation as linear cells. It imports no local `.py`
  modules and defines no helper functions; intermediate API payloads, prompts, outputs, and SQL are visible.
  The five-model loop is commented out, while the executable test cell uses only DeepSeek V3 and GPT-4.1 Mini.

The production client uses `httpx` to make OpenRouter's HTTP contract explicit. The teaching notebook uses
the official `openai` package with `base_url="https://openrouter.ai/api/v1"`. OpenRouter implements an
OpenAI-compatible API, so no OpenRouter-specific Python package is required.

## 3. GitHub setup

1. Push the project to GitHub.
2. Open **Settings > Secrets and variables > Actions**.
3. Add `OPENROUTER_API_KEY`, `TAVILY_API_KEY`, and `FOOTBALL_DATA_API_KEY`.
4. Open **Actions**, enable workflows, and run `Daily AI football analysis` manually once.
5. Confirm `data/championship.db` was committed. Scheduled workflows run only on the default branch.

The workflow runs the data job, not a permanent Gradio server. For a hosted webpage, deploy `app.py` to
Hugging Face Spaces, Render, or another persistent Python host, or publish a static report separately.

## 4. Cost, quality, and safety

- One match produces three Tavily searches, five analyst calls, and one master call. A busy match day can be
  expensive. Start with one date and inspect provider dashboards before enabling cron.
- Team news is capped at 4 results / 1,200 characters each, betting at 5 / 1,600, and tactics/weather at
  3 / 800. `ANALYST_MAX_TOKENS` and `MASTER_MAX_TOKENS` cap generated output; `MAX_MATCHES_PER_RUN` limits
  the daily multiplier.
- Prompts also cap Chinese response length: 180/600 characters for analyst summary/detail and 220/800 for
  master conclusion/analysis. This encourages graceful shortening before the hard token ceiling is reached.
- The largest cost lever is `ENABLED_MODEL_IDS`: three analysts instead of five cuts analyst calls by 40%.
  Keep a diverse panel, for example GPT, Claude, and DeepSeek, rather than three models from one provider.
- Search results are evidence, not a licensed real-time odds feed. For serious odds comparison, add a
  regulated odds API and record bookmaker, market, timestamp, currency, and jurisdiction.
- Predictions are not financial advice. Never portray confidence as certainty, never chase losses, and obey
  local age and gambling laws. `No bet` is a valid and often best recommendation.
- Evaluate the system after matches. Store actual scores later, then measure Brier score/log loss for 1X2
  probabilities and closing-line value for prices. Raw win rate alone encourages overconfident models.

## 5. Model availability

The requested slugs are kept exactly in `config.py`, but model catalogs and account access can change.
OpenRouter returns an error when a slug is unavailable. The panel catches that error; the master still runs
with remaining opinions. Before spending money, compare each slug with OpenRouter's current models endpoint
or dashboard and update the configuration. Do not silently substitute a different model because that would
make historical comparisons misleading.
