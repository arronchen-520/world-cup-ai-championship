"""Gradio browser for saved analyses and on-demand runs."""

from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

import gradio as gr

from config import ENABLE_GRADIO_RUN, GRADIO_SHARE, MATCH_TIMEZONE, MODELS
from database import get_day, get_leaderboard, initialize_database
from logging_config import configure_logging
from pipeline import run_for_date


logger = logging.getLogger(__name__)


def _date_string(value: str | datetime | None) -> str:
    if value is None:
        return datetime.now(ZoneInfo(MATCH_TIMEZONE)).date().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)[:10]


def _time_string(value: str | None) -> str:
    if not value:
        return "TBD"
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%H:%M")
    except ValueError:
        pass
    if "T" in text:
        return text.split("T", 1)[1][:5]
    parts = text.split()
    if len(parts) > 1 and parts[0].count("-") == 2:
        return parts[1][:5]
    return text


def _choices(rows: list[dict]) -> list[tuple[str, str]]:
    return [
        (f"{_time_string(row.get('kickoff_local') or row.get('kickoff'))} | {row['home_team']} vs {row['away_team']}", row["match_key"])
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
    logger.info("gradio.date.loaded", extra={"match_date": day, "saved_matches": len(rows)})
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
    logger.debug("gradio.match.shown", extra={"match_key": match_key})
    return [header, final_summary, final_detail, *opinions, _evaluation_markdown(row.get("evaluation"))]


def analyze_date(value: str | datetime | None):
    day_text = _date_string(value)
    day = date.fromisoformat(day_text)
    if day > datetime.now(ZoneInfo(MATCH_TIMEZONE)).date():
        raise gr.Error("Future matches are outside Phase 1.")
    logger.info("gradio.analysis.requested", extra={"match_date": day_text})
    try:
        results = run_for_date(day)
    except Exception as error:
        logger.exception("gradio.analysis.failed", extra={"match_date": day_text})
        raise gr.Error(f"Analysis failed: {type(error).__name__}: {error}") from error
    dropdown, _, rows, leaderboard = load_date(day_text)
    return (
        dropdown,
        f"Analysis complete: {len(results)} match(es) for {day_text}.",
        rows,
        leaderboard,
    )


def build_app() -> gr.Blocks:
    configure_logging()
    initialize_database()
    logger.info("gradio.app.build", extra={"model_count": len(MODELS)})
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
    @media (prefers-color-scheme: dark) {
      .gradio-container {
        --body-background-fill: #0f172a;
        --background-fill-primary: #0f172a;
        --background-fill-secondary: #111827;
        --block-background-fill: #111827;
        --block-border-color: #334155;
        --body-text-color: #e5e7eb;
        --input-background-fill: #111827;
        --input-border-color: #475569;
      }
      .gradio-container input,
      .gradio-container textarea,
      .gradio-container select {
        background: #111827 !important;
        color: #e5e7eb !important;
        border-color: #475569 !important;
      }
      .gradio-container input::placeholder,
      .gradio-container textarea::placeholder {
        color: #94a3b8 !important;
      }
      .model-card {
        border-color: #334155;
        background: linear-gradient(180deg,#111827,#0f172a);
      }
      .final-card {
        border-color: #22d3ee;
        background: linear-gradient(135deg,#0f172a,#111827);
      }
      .review-card {
        border-color: #475569;
        background: #111827;
      }
      .leaderboard-card {
        border-color: #f59e0b;
        background: linear-gradient(135deg,#1f2937,#111827);
      }
    }
    .dark .gradio-container,
    .gradio-container.dark {
      --body-background-fill: #0f172a;
      --background-fill-primary: #0f172a;
      --background-fill-secondary: #111827;
      --block-background-fill: #111827;
      --block-border-color: #334155;
      --body-text-color: #e5e7eb;
      --input-background-fill: #111827;
      --input-border-color: #475569;
    }
    .dark .gradio-container input,
    .dark .gradio-container textarea,
    .dark .gradio-container select,
    .gradio-container.dark input,
    .gradio-container.dark textarea,
    .gradio-container.dark select {
      background: #111827 !important;
      color: #e5e7eb !important;
      border-color: #475569 !important;
    }
    """
    gradio_major = int(gr.__version__.split(".", 1)[0])
    blocks_options = {"title": "AI World Cup Championship"}
    if gradio_major < 6:
        blocks_options["css"] = css

    with gr.Blocks(**blocks_options) as demo:
        rows_state = gr.State([])
        gr.HTML("<div class='hero'><h1>AI World Cup Championship</h1><p>Five analysts. One final whistle.</p></div>")
        gr.Markdown(
            "**免责声明：**本页面仅用于 AI Championship 模型比赛展示与娱乐研究，不构成博彩、投注、投资或财务建议。"
            "模型输出可能出错，赔率和赛况会变化，请勿将本页面内容作为下注依据。"
        )
        with gr.Row():
            day_input = gr.DateTime(label="Match date", value=today, include_time=False, type="string")
            load_button = gr.Button("Load saved", variant="secondary")
            if ENABLE_GRADIO_RUN:
                analyze_button = gr.Button("Run / refresh analysis", variant="primary")
        status = gr.Markdown()
        match_select = gr.Dropdown(label="Match", choices=[])
        match_header = gr.Markdown()
        with gr.Column(elem_classes="final-card"):
            gr.Markdown("## Master AI 最终结论")
            final_summary_box = gr.Markdown()
            with gr.Accordion("展开综合分析", open=False):
                final_detail_box = gr.Markdown()
        gr.Markdown("## 五模型并行观点")
        model_boxes = []
        with gr.Row():
            for model in MODELS:
                with gr.Column(elem_classes="model-card", min_width=260):
                    gr.Markdown(f"### {model['id']}")
                    summary_box = gr.Markdown(elem_classes="model-summary")
                    with gr.Accordion("展开详细分析", open=False):
                        detail_box = gr.Markdown()
                    model_boxes.extend([summary_box, detail_box])
        with gr.Column(elem_classes="review-card"):
            gr.Markdown("## 赛后 AI 复盘")
            evaluation_box = gr.Markdown()
        leaderboard_box = gr.Markdown(_leaderboard_markdown(), elem_classes="leaderboard-card")

        outputs = [match_header, final_summary_box, final_detail_box, *model_boxes, evaluation_box]
        date_outputs = [match_select, status, rows_state, leaderboard_box]
        load_button.click(load_date, day_input, date_outputs).then(
            show_match, [match_select, rows_state], outputs
        )
        if ENABLE_GRADIO_RUN:
            analyze_button.click(analyze_date, day_input, date_outputs).then(
                show_match, [match_select, rows_state], outputs
            )
        match_select.change(show_match, [match_select, rows_state], outputs)
        demo.load(load_date, day_input, date_outputs).then(
            show_match, [match_select, rows_state], outputs
        )
    demo.app_css = css
    demo.css_on_launch = gradio_major >= 6
    return demo


if __name__ == "__main__":
    demo = build_app()
    launch_options = {"css": demo.app_css} if demo.css_on_launch else {}
    launch_options["share"] = GRADIO_SHARE
    demo.launch(**launch_options)
