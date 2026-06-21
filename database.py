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
                status TEXT,
                actual_result_json TEXT,
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
            CREATE TABLE IF NOT EXISTS match_evaluations (
                match_key TEXT PRIMARY KEY REFERENCES matches(match_key),
                actual_result_json TEXT NOT NULL,
                ranking_json TEXT NOT NULL,
                analysis_text TEXT NOT NULL,
                evaluator_model TEXT NOT NULL,
                evaluated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS model_scores (
                match_key TEXT NOT NULL REFERENCES matches(match_key),
                model_id TEXT NOT NULL,
                rank INTEGER NOT NULL CHECK(rank BETWEEN 1 AND 5),
                points INTEGER NOT NULL CHECK(points BETWEEN 1 AND 5),
                reason TEXT NOT NULL,
                PRIMARY KEY (match_key, model_id)
            );
            CREATE TABLE IF NOT EXISTS model_standings (
                model_id TEXT PRIMARY KEY,
                evaluated_matches INTEGER NOT NULL,
                total_points INTEGER NOT NULL,
                average_score REAL NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        columns = {row[1] for row in db.execute("PRAGMA table_info(matches)")}
        for name in ("kickoff_local", "group_name", "venue", "status", "actual_result_json"):
            if name not in columns:
                db.execute(f"ALTER TABLE matches ADD COLUMN {name} TEXT")
        if "referees_json" not in columns:
            db.execute("ALTER TABLE matches ADD COLUMN referees_json TEXT NOT NULL DEFAULT '[]'")
        for legacy_name in ("city",):
            if legacy_name in columns:
                db.execute(f"ALTER TABLE matches DROP COLUMN {legacy_name}")


def save_match(match: dict[str, Any], path: Path = DATABASE_PATH) -> None:
    with connect(path) as db:
        db.execute(
            """INSERT INTO matches
               (match_key, match_date, kickoff, competition, group_name, home_team, away_team,
                kickoff_local, venue, status, actual_result_json, referees_json, source, raw_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(match_key) DO UPDATE SET
                 match_date=excluded.match_date, kickoff=excluded.kickoff,
                 kickoff_local=excluded.kickoff_local,
                 competition=excluded.competition, group_name=excluded.group_name,
                 home_team=excluded.home_team, away_team=excluded.away_team,
                 venue=excluded.venue, status=excluded.status,
                 actual_result_json=excluded.actual_result_json, referees_json=excluded.referees_json,
                 source=excluded.source, raw_json=excluded.raw_json,
                 updated_at=CURRENT_TIMESTAMP""",
            (
                match["match_key"], match["match_date"], match.get("kickoff"),
                match["competition"], match.get("group_name"), match["home_team"], match["away_team"],
                match.get("kickoff_local"), match.get("venue"), match.get("status"),
                json.dumps(match.get("actual_result")) if match.get("actual_result") else None,
                json.dumps(match.get("referees", [])),
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


def save_evaluation(
    match_key: str,
    actual_result: dict[str, Any],
    evaluation: dict[str, Any],
    evaluator_model: str,
    path: Path = DATABASE_PATH,
) -> None:
    """Persist one strict ranking and rebuild cumulative model standings atomically."""
    with connect(path) as db:
        db.execute(
            """INSERT INTO match_evaluations
               (match_key, actual_result_json, ranking_json, analysis_text, evaluator_model, evaluated_at)
               VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(match_key) DO UPDATE SET
                 actual_result_json=excluded.actual_result_json,
                 ranking_json=excluded.ranking_json,
                 analysis_text=excluded.analysis_text,
                 evaluator_model=excluded.evaluator_model,
                 evaluated_at=CURRENT_TIMESTAMP""",
            (
                match_key,
                json.dumps(actual_result),
                json.dumps(evaluation["ranking"], ensure_ascii=False),
                evaluation["overall_analysis"],
                evaluator_model,
            ),
        )
        db.execute("DELETE FROM model_scores WHERE match_key = ?", (match_key,))
        db.executemany(
            """INSERT INTO model_scores (match_key, model_id, rank, points, reason)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (match_key, item["model_id"], item["rank"], item["points"], item["reason"])
                for item in evaluation["ranking"]
            ],
        )
        db.execute("DELETE FROM model_standings")
        db.execute(
            """INSERT INTO model_standings
               (model_id, evaluated_matches, total_points, average_score, updated_at)
               SELECT model_id, COUNT(*), SUM(points), AVG(points), CURRENT_TIMESTAMP
               FROM model_scores GROUP BY model_id"""
        )


def get_pending_evaluation_dates(before_day: str, limit: int = 8, path: Path = DATABASE_PATH) -> list[str]:
    """Return recent analyzed match dates that still have at least one unevaluated match."""
    with connect(path) as db:
        rows = db.execute(
            """SELECT DISTINCT m.match_date
               FROM matches m
               JOIN analyses a USING (match_key)
               LEFT JOIN match_evaluations e USING (match_key)
               WHERE m.match_date < ? AND e.match_key IS NULL
               ORDER BY m.match_date DESC LIMIT ?""",
            (before_day, limit),
        ).fetchall()
    return [row[0] for row in rows]


def get_leaderboard(path: Path = DATABASE_PATH) -> list[dict[str, Any]]:
    with connect(path) as db:
        rows = db.execute(
            """SELECT model_id, evaluated_matches, total_points, average_score, updated_at
               FROM model_standings
               ORDER BY average_score DESC, total_points DESC, model_id"""
        ).fetchall()
    return [dict(row) for row in rows]


def get_day(day: str, path: Path = DATABASE_PATH) -> list[dict[str, Any]]:
    with connect(path) as db:
        rows = db.execute(
            """SELECT m.*, a.research_json, a.model_outputs_json,
                      a.final_output, a.analyzed_at,
                      e.actual_result_json AS evaluation_result_json,
                      e.ranking_json, e.analysis_text AS evaluation_analysis,
                      e.evaluator_model, e.evaluated_at
               FROM matches m LEFT JOIN analyses a USING (match_key)
               LEFT JOIN match_evaluations e USING (match_key)
               WHERE m.match_date = ? ORDER BY m.kickoff, m.match_key""",
            (day,),
        ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        item["raw"] = json.loads(item.pop("raw_json"))
        item["referees"] = json.loads(item.pop("referees_json"))
        actual_result_json = item.pop("actual_result_json")
        item["actual_result"] = json.loads(actual_result_json) if actual_result_json else None
        research_json = item.pop("research_json")
        model_outputs_json = item.pop("model_outputs_json")
        item["research"] = json.loads(research_json) if research_json else {}
        item["model_outputs"] = json.loads(model_outputs_json) if model_outputs_json else {}
        evaluation_result_json = item.pop("evaluation_result_json")
        ranking_json = item.pop("ranking_json")
        item["evaluation"] = None
        if ranking_json:
            item["evaluation"] = {
                "actual_result": json.loads(evaluation_result_json),
                "ranking": json.loads(ranking_json),
                "overall_analysis": item.pop("evaluation_analysis"),
                "evaluator_model": item.pop("evaluator_model"),
                "evaluated_at": item.pop("evaluated_at"),
            }
        else:
            item.pop("evaluation_analysis")
            item.pop("evaluator_model")
            item.pop("evaluated_at")
        results.append(item)
    return results
