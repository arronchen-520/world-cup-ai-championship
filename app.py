"""Gradio browser for saved analyses and on-demand runs."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import gradio as gr

from config import MATCH_TIMEZONE, MODELS
from database import get_day, get_leaderboard, initialize_database
from pipeline import run_for_date


def _date_string(value: str | datetime | None) -> str:
    if value is None:
        return datetime.now(ZoneInfo(MATCH_TIMEZONE)).date().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)[:10]


def _choices(rows: list[dict]) -> list[tuple[str, str]]:
    return [
        (f"{row.get('kickoff_local') or row.get('kickoff') or 'TBD'} | {row['home_team']} vs {row['away_team']}", row["match_key"])
        for row in rows
    ]


def _split_output(text: str | None, detail_heading: str) -> tuple[str, str]:
    if not text:
        return "_暂无分析。_", ""
    if detail_heading not in text:
        return text, ""
    summary, detail = text.split(detail_heading, 1)
    return summary.strip(), detail.strip()


def _evaluation_markdown(evaluation: dict | None) -> str:
    if not evaluation:
        return ""
    result = evaluation["actual_result"]
    outcome_labels = {"HOME_WIN": "主胜", "DRAW": "平局", "AWAY_WIN": "客胜"}
    lines = [
        f"**常规时间赛果：** {result['regulation_home']}–{result['regulation_away']} "
        f"({outcome_labels.get(result['regulation_outcome'], result['regulation_outcome'])})",
        "",
        "| 排名 | 模型 | 得分 | 赛后评价 |",
        "|---:|---|---:|---|",
    ]
    for item in sorted(evaluation["ranking"], key=lambda row: row["rank"]):
        reason = item["reason"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {item['rank']} | {item['model_id']} | {item['points']} | {reason} |")
    lines.extend(["", "**DeepSeek 综合复盘**", "", evaluation["overall_analysis"]])
    return "\n".join(lines)


def _leaderboard_markdown() -> str:
    standings = get_leaderboard()
    lines = [
        "## AI Championship 累计排行榜",
        "",
        "| 排名 | 模型 | 平均分 | 总分 | 已评分比赛 |",
        "|---:|---|---:|---:|---:|",
    ]
    if not standings:
        lines.append("| – | 暂无已完成评分 | – | – | – |")
    for rank, item in enumerate(standings, start=1):
        lines.append(
            f"| {rank} | {item['model_id']} | {item['average_score']:.2f} | "
            f"{item['total_points']} | {item['evaluated_matches']} |"
        )
    return "\n".join(lines)


def load_date(value: str | datetime | None):
    day = _date_string(value)
    if date.fromisoformat(day) > datetime.now(ZoneInfo(MATCH_TIMEZONE)).date():
        raise gr.Error("Please choose today or an earlier date.")
    rows = get_day(day)
    choices = _choices(rows)
    selected = choices[0][1] if choices else None
    status = f"Found {len(rows)} saved match(es) for {day}."
    return gr.update(choices=choices, value=selected), status, rows, _leaderboard_markdown()


def show_match(match_key: str | None, rows: list[dict] | None):
    row = next((item for item in (rows or []) if item["match_key"] == match_key), None)
    if not row:
        return ["_暂无已保存分析。_", *([""] * (len(MODELS) * 2 + 3))]
    location = row.get("venue") or "TBD"
    group = f" | {row['group_name']}" if row.get("group_name") else ""
    referees = ", ".join(
        f"{referee.get('name')} ({referee.get('role')})" if referee.get("role") else referee.get("name", "")
        for referee in row.get("referees", [])
        if referee.get("name")
    ) or "TBD"
    header = (
        f"## {row['home_team']} vs {row['away_team']}\n"
        f"**{row['competition']}**{group} | 比赛日时间 ({MATCH_TIMEZONE}): {row.get('kickoff_local') or 'TBD'} "
        f"| UTC: {row.get('kickoff') or 'TBD'} | 场地: {location} | 数据源: {row['source']}\n\n"
        f"**裁判:** {referees}"
    )
    opinions = []
    for model in MODELS:
        summary, detail = _split_output(row["model_outputs"].get(model["id"]), "## 详细分析")
        opinions.extend([summary, detail])
    final = row.get("final_output") or "_Analysis has not been generated yet._"
    final_summary, final_detail = _split_output(final, "## 综合分析")
    return [header, *opinions, final_summary, final_detail, _evaluation_markdown(row.get("evaluation"))]


def analyze_date(value: str | datetime | None):
    day_text = _date_string(value)
    day = date.fromisoformat(day_text)
    if day > datetime.now(ZoneInfo(MATCH_TIMEZONE)).date():
        raise gr.Error("Future matches are outside Phase 1.")
    try:
        results = run_for_date(day)
    except Exception as error:
        raise gr.Error(f"Analysis failed: {type(error).__name__}: {error}") from error
    dropdown, _, rows, leaderboard = load_date(day_text)
    return (
        dropdown,
        f"Analysis complete: {len(results)} match(es) for {day_text}.",
        rows,
        leaderboard,
    )


def build_app() -> gr.Blocks:
    initialize_database()
    today = datetime.now(ZoneInfo(MATCH_TIMEZONE)).date().isoformat()
    css = """
    .hero {text-align:center; padding:18px; border-radius:18px;
           background:linear-gradient(120deg,#071952,#088395); color:white}
    .model-card {border:1px solid #d8dee9; border-radius:14px; padding:12px; min-height:280px;
                 background:linear-gradient(180deg,#ffffff,#f7fbfc)}
    .model-summary {min-height:210px}
    .final-card {border:2px solid #088395; border-radius:14px; padding:16px;
                 background:linear-gradient(135deg,#f0ffff,#ffffff)}
    .review-card {border:1px solid #8e9aaf; border-radius:14px; padding:16px;
                  background:#f8f9fa}
    .leaderboard-card {border:2px solid #f4b942; border-radius:14px; padding:16px;
                       background:linear-gradient(135deg,#fff8dd,#ffffff)}
    """
    with gr.Blocks(title="AI World Cup Championship", css=css) as demo:
        rows_state = gr.State([])
        gr.HTML("<div class='hero'><h1>AI World Cup Championship</h1><p>Five analysts. One final whistle.</p></div>")
        gr.Markdown("预测仅供参考。赔率会变化，模型可能出错，任何投注都没有保证。")
        with gr.Row():
            day_input = gr.DateTime(label="Match date", value=today, include_time=False, type="string")
            load_button = gr.Button("Load saved", variant="secondary")
            analyze_button = gr.Button("Run / refresh analysis", variant="primary")
        status = gr.Markdown()
        match_select = gr.Dropdown(label="Match", choices=[])
        match_header = gr.Markdown()
        model_boxes = []
        for start in range(0, len(MODELS), 3):
            with gr.Row():
                for model in MODELS[start:start + 3]:
                    with gr.Column(elem_classes="model-card"):
                        gr.Markdown(f"### {model['id']}")
                        summary_box = gr.Markdown(elem_classes="model-summary")
                        with gr.Accordion("展开详细分析", open=False):
                            detail_box = gr.Markdown()
                        model_boxes.extend([summary_box, detail_box])
        with gr.Column(elem_classes="final-card"):
            gr.Markdown("## Master AI 最终结论")
            final_summary_box = gr.Markdown()
            with gr.Accordion("展开综合分析", open=False):
                final_detail_box = gr.Markdown()
        with gr.Column(elem_classes="review-card"):
            gr.Markdown("## 赛后 AI 复盘")
            evaluation_box = gr.Markdown()
        leaderboard_box = gr.Markdown(_leaderboard_markdown(), elem_classes="leaderboard-card")

        outputs = [match_header, *model_boxes, final_summary_box, final_detail_box, evaluation_box]
        date_outputs = [match_select, status, rows_state, leaderboard_box]
        load_button.click(load_date, day_input, date_outputs).then(
            show_match, [match_select, rows_state], outputs
        )
        analyze_button.click(analyze_date, day_input, date_outputs).then(
            show_match, [match_select, rows_state], outputs
        )
        match_select.change(show_match, [match_select, rows_state], outputs)
        demo.load(load_date, day_input, date_outputs).then(
            show_match, [match_select, rows_state], outputs
        )
    return demo


if __name__ == "__main__":
    demo = build_app()
    demo.launch()
