# AI World Cup Championship

A Gradio dashboard that discovers football fixtures, researches each matchup with Tavily,
collects independent opinions through OpenRouter, and asks a master model for a final synthesis.

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
match research. Analyst and master outputs are Simplified Chinese. `MAX_MATCHES_PER_RUN=16` limits paid runs.

Default cost controls are five Tavily results per query, 1,800 characters per result, 1,000 output tokens per
analyst, and 1,500 output tokens for the master. `ENABLED_MODEL_IDS` can reduce the five-model panel; see
`.env.example` for every override.

Model slugs change over time and availability varies by OpenRouter account. A failed analyst is
recorded as unavailable while the other analysts continue. Verify the `MODELS` entries against your
OpenRouter model catalog before the first paid run.

See [WALKTHROUGH.md](WALKTHROUGH.md) for the architecture, every file and function, GitHub setup,
cost controls, limitations, and responsible-use notes.
