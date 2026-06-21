# AI World Cup Championship

A Gradio dashboard that discovers football fixtures, researches each matchup with Tavily,
collects independent opinions through OpenRouter, and asks a master model for a final synthesis.
After matches finish, DeepSeek ranks the five analyst predictions against the regulation-time result and
the dashboard tracks cumulative 5-to-1 scores and average points. The master answer is never scored.

## Quick start

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# Fill in keys in .env
python run_daily.py
python app.py
```

Open the local URL printed by Gradio. Use **Run / refresh analysis** to populate a date,
or **Load saved** to browse existing SQLite history.

For the cell-by-cell version, open `ai_world_cup_walkthrough.ipynb`. It imports only installed
packages and the Python standard library; it never imports the project's `.py` files. The notebook
uses `load_dotenv()` and linear executable cells rather than hiding the workflow inside helper functions.

## Required secrets

- `OPENROUTER_API_KEY`: analyst and master-model calls.
- `TAVILY_API_KEY`: team news, form, injuries, and odds research.
- `FOOTBALL_DATA_API_KEY`: structured World Cup fixtures from football-data.org.

football-data.org is the only fixture source. One request returns stable match IDs, teams, UTC kickoff,
venue, stage, and group; the documented JSON fields are normalized directly. Tavily is used only for
match research. Analyst and master outputs are Simplified Chinese.
Every daily run also refreshes yesterday plus any older unevaluated match days. Only `FINISHED` fixtures with
five stored analyst outputs are judged; late matches remain queued for a later run.

Team news, betting, and tactical context all use Tavily `advanced` depth. The app does not set `max_results`,
truncate returned `content`, cap matches per run, or send an OpenRouter `max_tokens` value. Tavily therefore
uses its service default result count, and models receive the full extracted search content. Generated Tavily
answers and betting-domain allowlists remain disabled. The notebook prints every query/research/prompt character
count so limits can later be chosen from observed runs rather than guessed in advance.

Model slugs change over time and availability varies by OpenRouter account. A failed analyst is
recorded as unavailable while the other analysts continue. Verify the `MODELS` entries against your
OpenRouter model catalog before the first paid run.

See [WALKTHROUGH.md](WALKTHROUGH.md) for the architecture, every file and function, GitHub setup,
cost observations, limitations, and responsible-use notes.
