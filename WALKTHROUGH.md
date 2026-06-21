# Technical walkthrough

## 1. System flow

1. `run_daily.py` determines today's match date in `America/Chicago`.
2. The pipeline refreshes yesterday and older unevaluated dates. Finished matches with five analyst reports
   are ranked by DeepSeek; SQLite assigns 5, 4, 3, 2, and 1 points and rebuilds cumulative standings.
3. `pipeline.run_for_date()` requests the selected World Cup date from football-data.org and directly
   normalizes stable IDs, teams, UTC kickoff, venue, stage, and group from its JSON response.
4. Three Tavily searches run concurrently for every match: team availability/form, betting markets,
   and tactical/venue context.
5. All configured analyst models run concurrently through OpenRouter and reuse the bounded Basic results.
6. The master model receives those results and every analyst opinion, then writes a calibrated synthesis.
7. Fixtures, raw evidence, analyses, evaluations, and standings are upserted into SQLite.
8. `app.py` displays analyst cards, the master answer, post-match review, and cumulative leaderboard.

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
- `initialize_database(path)` creates fixture/analysis tables plus `match_evaluations`, per-match
  `model_scores`, and cumulative `model_standings`. Existing databases receive the new result columns.
- `save_match(match, path)` performs an upsert keyed by the football-data.org match ID, so reruns update
  rather than duplicate.
- `save_analysis(...)` upserts research, panel answers, and final recommendation as JSON/text.
- `save_evaluation(...)` stores the actual score, strict ranking, explanations, and 5-to-1 points, then
  rebuilds cumulative match counts, total points, and averages in the same transaction.
- `get_pending_evaluation_dates(...)` finds recent analyzed dates containing unscored matches.
- `get_leaderboard()` reads the persisted aggregate standings in average-score order.
- `get_day(day, path)` uses a left join so newly discovered matches still appear before analysis succeeds.
  It converts stored JSON strings back into Python dictionaries and attaches optional evaluation data.

SQLite is preferable to one JSON file per day because writes are transactional, date queries are indexed,
and the schema can later gain results, evaluation scores, or users. It still remains a portable single file.

### `services.py`

- `openrouter_chat(model, messages, temperature)` posts the OpenAI-compatible chat payload. Tenacity
  retries transient failures up to three times with exponential backoff.
- `get_fixtures_football_data(day)` converts the Central-time day boundaries to UTC, requests every provider date
  touched by that interval, filters with exact timestamps, and normalizes the remaining fixtures.
- `extract_actual_result(raw_match)` accepts only `FINISHED` matches and selects the 90-minute score. For
  knockout matches, `regularTime` takes precedence over final scores that may include extra time.
- Referee name, provider role, and nationality are copied into each normalized fixture and stored as JSON.
- `research_match(match)` launches three Tavily searches in a thread pool. Search is I/O-bound, so threads
  reduce wall-clock time without complicated async code. Queries include both teams, World Cup 2026, the
  Central match date, and the specific evidence category; social-media domains are excluded. `basic` depth
  costs one Tavily credit per search and returns one NLP page summary per URL. Each returned group carries an
  explicit category and query so downstream models can distinguish team, betting, and context evidence.
- Query strings intentionally use compact entity-and-intent terms rather than conversational questions:
  team names and tournament identify the entity, while terms such as `predicted lineups` or `double chance`
  steer retrieval. Each query remains below Tavily's recommended 400-character limit.
- `include_answer=False` disables Tavily's separate LLM-generated answer. The response still contains ranked
  `results`, each with title, URL, relevance score, and `content`; those four bounded fields are retained as
  evidence for the analyst panel, master, and audit storage.
- Betting search names the markets the analysts must compare: 1X2, double chance, draw-no-bet, Asian
  handicap, totals 1.5/2.5/3.5, and both-teams-to-score. These terms steer semantic retrieval rather than
  guarantee that every result contains every market. Betting search excludes social sites but has no domain
  allowlist, because strict filtering often removes all relevant odds pages.
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
- `evaluate_analysts(match, actual_result, outputs)` sends only the five analyst reports, never the master,
  to DeepSeek. Pydantic requires each model and each rank exactly once. Python assigns `6 - rank` points so
  the language model cannot miscalculate the 5-to-1 score.

### `pipeline.py`

- `refresh_and_evaluate_previous(day)` refreshes yesterday plus up to eight stored backlog dates. At Chicago
  midnight, a 23:00 kickoff may still be running, so unfinished matches remain queued for a later daily run.
- `run_for_date(day)` owns the end-to-end sequence. It initializes storage, evaluates eligible past fixtures,
  then discovers fixtures,
  researches each match, gets panel opinions, calls the master at lower
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
- `_evaluation_markdown(evaluation)` renders the actual score, strict ranking, points, and DeepSeek review;
  it returns an empty string until a result exists.
- `_leaderboard_markdown()` renders persisted average score, total score, and evaluated match count.
- `load_date(value)` blocks future dates, queries SQLite, and updates dropdown/state components.
- `show_match(match_key, rows)` returns values in exactly the same order as the Gradio output components.
- `analyze_date(value)` validates the date, runs the pipeline, reloads storage, and converts failures into a
  visible Gradio error.
- `build_app()` constructs the themed Blocks interface and wires load, run, selection-change, and startup
  events. Five analyst cards are produced from `MODELS`, followed by a permanent post-match review card and
  the global leaderboard.

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
- `tests/test_evaluation.py` mocks OpenRouter and verifies strict ranking plus deterministic 5-to-1 points.
- `tests/test_football_data.py` verifies Central-day filtering and regulation-time score extraction.
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

- Before kickoff, one match produces three Tavily searches, five analyst calls, and one master call. After
  completion it produces one additional DeepSeek evaluation call.
- Team news is capped at 4 results / 1,200 characters each, betting at 5 / 1,600, and tactics/weather at
  3 / 800. These Basic-search caps bound the shared input without another LLM summarizer. Output caps are
  1,200 per analyst, 1,800 for the master, and 1,800 for the post-match evaluator;
  `MAX_MATCHES_PER_RUN=8` limits the daily multiplier.
- Prompts also cap Chinese response length: 180/600 characters for analyst summary/detail and 220/800 for
  master conclusion/analysis. This encourages graceful shortening before the hard token ceiling is reached.
- The largest cost lever is `ENABLED_MODEL_IDS`: three analysts instead of five cuts analyst calls by 40%.
  Keep a diverse panel, for example GPT, Claude, and DeepSeek, rather than three models from one provider.
- Search results are evidence, not a licensed real-time odds feed. For serious odds comparison, add a
  regulated odds API and record bookmaker, market, timestamp, currency, and jurisdiction.
- Predictions are not financial advice. Never portray confidence as certainty, never chase losses, and obey
  local age and gambling laws. `No bet` is a valid and often best recommendation.
- The 5-to-1 leaderboard is a comparative, LLM-judged championship score, not a statistical calibration
  metric. A later phase should additionally calculate deterministic Brier score/log loss for 1X2 probabilities.
- DeepSeek is both the inexpensive judge and one possible panel contestant, so self-evaluation bias remains
  possible despite the fixed rubric. A later phase can use an independent judge or multi-judge consensus.

## 5. Model availability

The requested slugs are kept exactly in `config.py`, but model catalogs and account access can change.
OpenRouter returns an error when a slug is unavailable. The panel catches that error; the master still runs
with remaining opinions. Before spending money, compare each slug with OpenRouter's current models endpoint
or dashboard and update the configuration. Do not silently substitute a different model because that would
make historical comparisons misleading.
