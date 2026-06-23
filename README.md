# AI World Cup Championship

Five AI analysts preview every World Cup match, a master model synthesizes their opinions, and a post-match judge scores the analysts after the final whistle.

This project is a production-ready Gradio app for the FIFA World Cup 2026. It combines structured fixtures, live web research, bookmaker odds, multi-model reasoning, SQLite persistence, and a public dashboard.

## Highlights

- Daily FIFA World Cup fixtures from football-data.org.
- Team news, injuries, form, tactics, venue, weather, and referee context from Tavily.
- Structured Bovada odds from The Odds API for `h2h`, `spreads`, and `totals`.
- Five independent analyst models through OpenRouter.
- One master model that creates the final recommendation.
- Post-match evaluation with a fixed 5-to-1 scoring table.
- SQLite storage for match history, model outputs, final recommendations, reviews, and leaderboard.
- Gradio UI for local use, temporary public sharing, or hosted deployment.

## 中文简介

这是一个世界杯 AI 预测锦标赛项目：每天抓取世界杯赛程，检索球队新闻和战术信息，读取结构化赔率，让五个模型分别给出预测，再由一个 master model 做最终综合。比赛结束后，系统会根据真实常规时间赛果给五个模型打分，并在 Gradio 页面展示排行榜。

适合展示 AI 多模型协作、体育数据管线、赔率信息增强、自动复盘和可视化 dashboard。

## Data Sources

- Fixtures: football-data.org
- Research: Tavily
- Odds: The Odds API, default `soccer_fifa_world_cup`, `bookmakers=bovada`, `markets=h2h,spreads,totals`
- LLMs: OpenRouter
- Storage: local SQLite database under `data/`

The initial GitHub upload should not include historical database files. The app creates `data/championship.db` automatically when it runs.

## Quick Start

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create a local `.env` file in the project root:

```env
OPENROUTER_API_KEY=your_openrouter_key
TAVILY_API_KEY=your_tavily_key
FOOTBALL_DATA_API_KEY=your_football_data_key
ODDS_API_KEY=your_the_odds_api_key

MATCH_TIMEZONE=America/Chicago
LOG_LEVEL=INFO
```

Run the first production analysis from June 23, 2026:

```powershell
python run_daily.py --date 2026-06-23
python app.py
```

Open the local Gradio URL printed in the terminal.

## Make The Gradio App Shareable

For a temporary public Gradio link from your local machine:

```powershell
$env:GRADIO_SHARE="true"
python app.py
```

Gradio will print a public `https://...gradio.live` URL. Keep the terminal running while sharing it.

For a more stable public website, deploy this repo to Hugging Face Spaces:

1. Create a new Space with SDK `Gradio`.
2. Upload the repo files.
3. Add the same environment variables as Space secrets.
4. Set the Space app file to `app.py`.
5. Run `python run_daily.py --date 2026-06-23` locally or through your scheduled job to populate data.

## GitHub Setup

1. Create an empty GitHub repository.
2. Push this project after committing the cleaned repo.
3. In GitHub, open `Settings > Secrets and variables > Actions`.
4. Add these repository secrets:
   - `OPENROUTER_API_KEY`
   - `TAVILY_API_KEY`
   - `FOOTBALL_DATA_API_KEY`
   - `ODDS_API_KEY`
5. Go to `Actions` and enable workflows.
6. Manually run `Daily AI football analysis` with date `2026-06-23` for the first run.

The workflow also runs near Chicago midnight. It refreshes prior unevaluated matches, analyzes the current match date, and can commit the updated SQLite database back to the repository.

## Upload Commands

```powershell
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git branch -M main
git push -u origin main
```

If the remote already exists:

```powershell
git remote set-url origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

## Production Notes

- Do not commit `.env`, local notebooks, personal walkthrough files, or generated `data/` files.
- The app uses regulation-time predictions only. Extra time and penalties are excluded from scoring.
- Odds are evidence for model reasoning, not betting advice.
- `No bet` is a valid recommendation when the edge is unclear.
- Model slugs can change on OpenRouter; verify availability before a paid run.

## 中文运行说明

第一次上传 GitHub 前，保持 `data/` 为空，不上传本地数据库和测试 JSON。

本地运行：

```powershell
python run_daily.py --date 2026-06-23
python app.py
```

临时分享 Gradio 页面：

```powershell
$env:GRADIO_SHARE="true"
python app.py
```

GitHub Actions 需要设置四个 secrets：`OPENROUTER_API_KEY`、`TAVILY_API_KEY`、`FOOTBALL_DATA_API_KEY`、`ODDS_API_KEY`。第一次手动运行 workflow 时，date 填 `2026-06-23`。
