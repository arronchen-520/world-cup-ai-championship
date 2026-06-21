"""SQLite persistence for fixtures, research, and model predictions."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from config import DATABASE_PATH


@contextmanager
def connect(path: Path = DATABASE_PATH) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def initialize_database(path: Path = DATABASE_PATH) -> None:
    with connect(path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS matches (
                match_key TEXT PRIMARY KEY,
                match_date TEXT NOT NULL,
                kickoff TEXT,
                kickoff_local TEXT,
                competition TEXT NOT NULL,
                group_name TEXT,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                venue TEXT,
                referees_json TEXT NOT NULL DEFAULT '[]',
                source TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(match_date);
            CREATE TABLE IF NOT EXISTS analyses (
                match_key TEXT PRIMARY KEY REFERENCES matches(match_key),
                research_json TEXT NOT NULL,
                model_outputs_json TEXT NOT NULL,
                final_output TEXT NOT NULL,
                analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        columns = {row[1] for row in db.execute("PRAGMA table_info(matches)")}
        for name in ("kickoff_local", "group_name", "venue"):
            if name not in columns:
                db.execute(f"ALTER TABLE matches ADD COLUMN {name} TEXT")
        if "referees_json" not in columns:
            db.execute("ALTER TABLE matches ADD COLUMN referees_json TEXT NOT NULL DEFAULT '[]'")
        for legacy_name in ("status", "city"):
            if legacy_name in columns:
                db.execute(f"ALTER TABLE matches DROP COLUMN {legacy_name}")


def save_match(match: dict[str, Any], path: Path = DATABASE_PATH) -> None:
    with connect(path) as db:
        db.execute(
            """INSERT INTO matches
               (match_key, match_date, kickoff, competition, group_name, home_team, away_team,
                kickoff_local, venue, referees_json, source, raw_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(match_key) DO UPDATE SET
                 match_date=excluded.match_date, kickoff=excluded.kickoff,
                 kickoff_local=excluded.kickoff_local,
                 competition=excluded.competition, group_name=excluded.group_name,
                 home_team=excluded.home_team, away_team=excluded.away_team,
                 venue=excluded.venue, referees_json=excluded.referees_json,
                 source=excluded.source, raw_json=excluded.raw_json,
                 updated_at=CURRENT_TIMESTAMP""",
            (
                match["match_key"], match["match_date"], match.get("kickoff"),
                match["competition"], match.get("group_name"), match["home_team"], match["away_team"],
                match.get("kickoff_local"), match.get("venue"), json.dumps(match.get("referees", [])),
                match["source"], json.dumps(match.get("raw", {})),
            ),
        )


def save_analysis(
    match_key: str,
    research: dict[str, Any],
    model_outputs: dict[str, str],
    final_output: str,
    path: Path = DATABASE_PATH,
) -> None:
    with connect(path) as db:
        db.execute(
            """INSERT INTO analyses
               (match_key, research_json, model_outputs_json, final_output, analyzed_at)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(match_key) DO UPDATE SET
                 research_json=excluded.research_json,
                 model_outputs_json=excluded.model_outputs_json,
                 final_output=excluded.final_output,
                 analyzed_at=CURRENT_TIMESTAMP""",
            (match_key, json.dumps(research), json.dumps(model_outputs), final_output),
        )


def get_day(day: str, path: Path = DATABASE_PATH) -> list[dict[str, Any]]:
    with connect(path) as db:
        rows = db.execute(
            """SELECT m.*, a.research_json, a.model_outputs_json,
                      a.final_output, a.analyzed_at
               FROM matches m LEFT JOIN analyses a USING (match_key)
               WHERE m.match_date = ? ORDER BY m.kickoff, m.match_key""",
            (day,),
        ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        item["raw"] = json.loads(item.pop("raw_json"))
        item["referees"] = json.loads(item.pop("referees_json"))
        item["research"] = json.loads(item.pop("research_json")) if item["research_json"] else {}
        item["model_outputs"] = json.loads(item.pop("model_outputs_json")) if item["model_outputs_json"] else {}
        results.append(item)
    return results
