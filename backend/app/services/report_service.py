"""
Standalone HTML session report generator.

Produces a self-contained HTML file with aggregate statistics only —
no player names, no individual scores, no personally identifiable information.
Answer distributions are reconstructed from session_scores.answer_data in MySQL
(Redis may have expired for completed sessions).
"""

from __future__ import annotations

import base64
import html
import re
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.course import Course
from ..models.game import Game
from ..models.image import Image
from ..models.session import SessionQuestion
from ..models.session import GameSession, SessionScore
from ..schemas.admin import NEW_TYPES, OPTION_IMAGES, PROMPT_IMAGE, option_label
from .game_service import answer_reveal, closest_guess_result, validate_answer


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _esc(s: object) -> str:
    return html.escape(str(s) if s is not None else "")


def _levenshtein(a: str, b: str) -> int:
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            temp = dp[j]
            dp[j] = prev if a[i - 1] == b[j - 1] else 1 + min(prev, dp[j], dp[j - 1])
            prev = temp
    return dp[n]


def _answer_reveal(q: SessionQuestion) -> dict:
    """Mirror of gateway._answer_reveal — derive correct-answer facts from answer_data."""
    if q.grading_type == "COMPLETENESS":
        return {"type": "completeness"}
    if q.type == "multiple_choice":
        pts: list[float] = q.answer_data.get("answer_points", [])
        return {
            "type": "multiple_choice",
            "correctIndices": [i for i, p in enumerate(pts) if p >= q.points_value],
        }
    if q.type == "true_false":
        pts_map: dict = q.answer_data.get("answer_points", {})
        return {
            "type": "true_false",
            "correctValue": pts_map.get("true", 0) >= q.points_value,
        }
    if q.type == "fill_in_the_blank":
        return {
            "type": "fill_in_the_blank",
            "acceptedAnswers": q.answer_data.get("acceptedAnswers", []),
            "editDistance": q.answer_data.get("editDistance", 0),
        }
    if q.type in NEW_TYPES:
        return answer_reveal(q)
    return {}


def _fmt_num(v: float) -> str:
    """Design §5.8: at most two decimals, trailing zeros removed, no -0."""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


def _new_type_summary(q: SessionQuestion, q_scores: list[SessionScore]) -> str:
    """One-line ACCURACY summary for a T7 question, computed from its score rows
    (_extract_answer_key has no entries for these types)."""
    if q.grading_type != "ACCURACY":
        return ""
    if q.type == "pin_drop":
        target = q.answer_data.get("target", {})
        correct = sum(1 for s in q_scores if s.is_correct)
        text = f"Target ({target.get('x')}, {target.get('y')}) · {correct} correct"
    else:
        bands = {"full": 0, "partial": 0, "miss": 0}
        for s in q_scores:
            ans = s.answer_data or {}
            if validate_answer(q, ans) is None:
                _, band = closest_guess_result(
                    ans["value"], q.answer_data, q.points_value
                )
                bands[band] += 1
        unit = (q.config or {}).get("unit")
        correct = _fmt_num(q.answer_data["correct"]) + (f" {unit}" if unit else "")
        text = (
            f"Correct {correct} · full {bands['full']} · "
            f"partial {bands['partial']} · miss {bands['miss']}"
        )
    return f'<div class="q-stats">{_esc(text)}</div>'


def _extract_answer_key(q_type: str, answer_data: dict | None) -> str | None:
    """Convert a player's answer_data blob into the distribution key used for charting."""
    if not answer_data:
        return None
    if q_type == "multiple_choice":
        idx = answer_data.get("selectedIndex")
        return str(idx) if idx is not None else None
    if q_type == "true_false":
        val = answer_data.get("selectedValue")
        return "true" if val else "false" if val is not None else None
    if q_type == "fill_in_the_blank":
        text = answer_data.get("text", "").strip()
        return text.lower() if text else None
    return None


# ---------------------------------------------------------------------------
# Images (T8 design §11)
# ---------------------------------------------------------------------------

# Each use embeds its own data: URI (plain HTML can't share one), so cap the total.
REPORT_IMAGE_BUDGET = 20 * 1_048_576


class _ImageEmbedder:
    """Turns snapshot image IDs into <img> tags with data: URIs, old versions included.
    Pin Drop backgrounds are not drawn (the report has no canvas)."""

    def __init__(self, rows: dict[int, tuple[str, bytes]], known: set[int]):
        # rows holds the bytes of the images that fit the budget; known is every
        # image ID that still exists.
        self.rows = rows
        self.known = known
        self.used = 0

    def tag(self, image_id: object, css_class: str, alt: str) -> str:
        if not isinstance(image_id, int) or image_id not in self.known:
            return '<p class="img-note">[image removed]</p>'
        row = self.rows.get(image_id)
        if row is None or self.used + len(row[1]) > REPORT_IMAGE_BUDGET:
            return '<p class="img-note">[image omitted: report size limit]</p>'
        content_type, data = row
        self.used += len(data)
        uri = f"data:{content_type};base64,{base64.b64encode(data).decode('ascii')}"
        return f'<img class="{css_class}" src="{uri}" alt="{_esc(alt)}">'

    def question_images(self, q: SessionQuestion) -> str:
        config = q.config or {}
        parts = []
        if PROMPT_IMAGE in config:
            parts.append(self.tag(config[PROMPT_IMAGE], "q-img", "Question image"))
        option_ids = config.get(OPTION_IMAGES)
        if isinstance(option_ids, list) and any(i is not None for i in option_ids):
            figures = [
                f'<figure class="opt-fig">{self.tag(i, "opt-img", option_label(n))}'
                f"<figcaption>{_OPTION_LETTERS[n] if n < 26 else n + 1}</figcaption>"
                "</figure>"
                for n, i in enumerate(option_ids)
                if i is not None
            ]
            parts.append('<div class="opt-imgs">' + "".join(figures) + "</div>")
        return "".join(parts)


def _image_uses(q: SessionQuestion) -> list[object]:
    """The image IDs question_images tags, in the same order (one entry per use)."""
    config = q.config or {}
    uses = [config[PROMPT_IMAGE]] if PROMPT_IMAGE in config else []
    option_ids = config.get(OPTION_IMAGES)
    if isinstance(option_ids, list):
        uses += [i for i in option_ids if i is not None]
    return uses


async def _load_report_images(
    db: AsyncSession, questions: list[SessionQuestion]
) -> tuple[dict[int, tuple[str, bytes]], set[int]]:
    """(bytes of the images the report embeds, every image ID that still exists).
    Sizes come first, so only bytes within REPORT_IMAGE_BUDGET are loaded."""
    uses = [i for q in questions for i in _image_uses(q) if isinstance(i, int)]
    if not uses:
        return {}, set()
    meta = await db.execute(
        select(Image.id, Image.content_type, Image.size_bytes).where(
            Image.id.in_(set(uses))
        )
    )
    sizes = {i: size for i, _ct, size in meta.all()}
    # Replay the embedder's budget walk to find the images it will embed.
    fit: set[int] = set()
    used = 0
    for i in uses:
        if i in sizes and used + sizes[i] <= REPORT_IMAGE_BUDGET:
            used += sizes[i]
            fit.add(i)
    rows: dict[int, tuple[str, bytes]] = {}
    if fit:
        result = await db.execute(
            select(Image.id, Image.content_type, Image.data).where(Image.id.in_(fit))
        )
        rows = {i: (ct, data) for i, ct, data in result.all()}
    return rows, set(sizes)


def _option_text(opt: object, index: int) -> str:
    """Option text, or "Option A" for an image-only option (T8 §5 display label)."""
    text = opt if isinstance(opt, str) else ""
    return text if text.strip() else option_label(index)


# ---------------------------------------------------------------------------
# Histogram helpers (mirrors GameOverPage logic)
# ---------------------------------------------------------------------------

_TARGET_BUCKETS = 8


def _choose_bin_width(ceiling: int) -> int:
    N = ceiling + 1
    best_w, best_diff = 1, float("inf")
    w = 1
    while w * w <= N:
        if N % w == 0:
            for cand in (w, N // w):
                nb = N // cand
                if nb < 2:
                    continue
                d = abs(nb - _TARGET_BUCKETS)
                if d < best_diff:
                    best_diff, best_w = d, cand
        w += 1
    if best_diff > _TARGET_BUCKETS:
        best_w = max(1, -(-N // _TARGET_BUCKETS))
    return best_w


def _build_histogram(scores: list[float], max_possible: float) -> list[dict]:
    ceiling = max(int(max_possible), 1)
    width = _choose_bin_width(ceiling)
    num_buckets = -(-(ceiling + 1) // width)
    buckets = []
    for i in range(num_buckets):
        start = i * width
        end = min(start + width - 1, ceiling)
        label = str(start) if start == end else f"{start}–{end}"
        buckets.append({"label": label, "count": 0})
    for s in scores:
        idx = min(int(s) // width, num_buckets - 1)
        buckets[idx]["count"] += 1
    return buckets


# ---------------------------------------------------------------------------
# HTML rendering helpers
# ---------------------------------------------------------------------------

_OPTION_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _render_bar_chart(
    q: SessionQuestion, dist: dict[str, int], reveal: dict, total_players: int
) -> str:
    options = q.config.get("options", [])

    if q.type == "multiple_choice":
        bars = [
            {
                "label": f"{_OPTION_LETTERS[i]} {_esc(_option_text(opt, i))}",
                "count": dist.get(str(i), 0),
                "correct": (i in reveal.get("correctIndices", []))
                if reveal.get("type") == "multiple_choice"
                else None,
            }
            for i, opt in enumerate(options)
        ]
    elif q.type == "true_false":
        cv = reveal.get("correctValue") if reveal.get("type") == "true_false" else None
        bars = [
            {"label": "True", "count": dist.get("true", 0), "correct": cv is True},
            {"label": "False", "count": dist.get("false", 0), "correct": cv is False},
        ]
    else:
        bars = []

    if not bars:
        return ""

    max_count = max((b["count"] for b in bars), default=0) or 1
    MAX_BAR_W = 100  # percentage

    rows = []
    for bar in bars:
        pct = int((bar["count"] / max_count) * MAX_BAR_W)
        min_pct = 3 if bar["count"] > 0 else 0
        display_pct = max(pct, min_pct)

        if bar["correct"] is True:
            fill_col = "#166534"
            mark = '<span class="bar-correct-mark">✓ Correct</span>'
        elif bar["correct"] is False:
            fill_col = "#1e293b"
            mark = '<span class="bar-correct-mark"></span>'
        else:
            fill_col = "#3730a3"
            mark = ""

        count_inner = (
            f'<span class="bar-count">{bar["count"]}</span>' if bar["count"] > 0 else ""
        )
        rows.append(
            f'<div class="bar-row">'
            f'<span class="bar-label">{bar["label"]}</span>'
            f'<div class="bar-track">'
            f'<div class="bar-fill" style="width:{display_pct}%;background:{fill_col}">{count_inner}</div>'
            f"</div>"
            f"{mark}"
            f"</div>"
        )

    return '<div class="bar-chart">' + "".join(rows) + "</div>"


def _render_word_cloud(q: SessionQuestion, dist: dict[str, int], reveal: dict) -> str:
    entries = sorted(dist.items(), key=lambda kv: -kv[1])
    accepted = [a.lower() for a in reveal.get("acceptedAnswers", [])]
    edit_dist = reveal.get("editDistance", 0)

    # Build the correct-answer note first — shown regardless of whether anyone answered
    parts = []
    if accepted and reveal.get("type") == "fill_in_the_blank":
        answer_str = " / ".join(_esc(a) for a in reveal["acceptedAnswers"])
        edit_note = (
            f' <span style="color:#64748b;font-size:0.85em">(±{edit_dist})</span>'
            if edit_dist > 0
            else ""
        )
        parts.append(
            f'<p class="wc-answer-note">Correct answer: '
            f'<span class="wc-answer">{answer_str}</span>{edit_note}</p>'
        )

    if not entries:
        parts.append('<p class="wc-empty">No answers submitted</p>')
        return "\n".join(parts)

    max_count = max(c for _, c in entries) or 1

    def is_correct(word: str) -> bool:
        return any(_levenshtein(word.lower(), a) <= edit_dist for a in accepted)

    def font_size(count: int) -> str:
        f = count / max_count
        if f > 0.75:
            return "2.4rem"
        if f > 0.5:
            return "1.9rem"
        if f > 0.25:
            return "1.4rem"
        if f > 0.1:
            return "1.1rem"
        return "0.9rem"

    parts.append('<div class="word-cloud">')
    for word, count in entries:
        css_class = "wc-word correct" if is_correct(word) else "wc-word"
        s = "s" if count != 1 else ""
        parts.append(
            f'<span class="{css_class}" style="font-size:{font_size(count)}" '
            f'title="{count} player{s}">{_esc(word)}</span>'
        )
    parts.append("</div>")
    return "\n".join(parts)


def _render_histogram(buckets: list[dict]) -> str:
    max_count = max((b["count"] for b in buckets), default=0) or 1
    MAX_H = 160  # max bar height px

    bar_cols, label_cols = [], []
    for b in buckets:
        bar_h = max(int((b["count"] / max_count) * MAX_H), 2 if b["count"] > 0 else 0)
        count_str = str(b["count"]) if b["count"] > 0 else ""
        bar_cols.append(
            f'<div class="hist-col">'
            f'<span class="hist-count">{count_str}</span>'
            f'<div class="hist-bar" style="height:{bar_h}px"></div>'
            f"</div>"
        )
        label_cols.append(f'<div class="hist-label">{_esc(b["label"])}</div>')

    return (
        '<div class="histogram">'
        + "".join(bar_cols)
        + "</div>"
        + '<div class="hist-labels">'
        + "".join(label_cols)
        + "</div>"
        + '<p class="hist-axis">Score (points) →</p>'
    )


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',sans-serif;
  background:#0f172a;color:#e2e8f0;line-height:1.6;padding:48px 24px;
}
.container{max-width:980px;margin:0 auto}

/* ── Header ── */
.rpt-header{text-align:center;margin-bottom:56px;padding-bottom:40px;border-bottom:1px solid #1e293b}
.rpt-header h1{font-size:2.4rem;font-weight:900;color:#f1f5f9;margin-bottom:12px}
.rpt-meta{color:#64748b;font-size:0.9rem}
.rpt-meta .sep{margin:0 10px;color:#1e293b}

/* ── Section label ── */
.section-label{
  font-size:0.65rem;text-transform:uppercase;letter-spacing:0.15em;
  color:#334155;margin-bottom:20px;
}

/* ── Question cards ── */
.q-card{
  background:#1e293b;border:1px solid #334155;border-radius:16px;
  padding:32px 36px;margin-bottom:24px;
}
.q-meta{display:flex;align-items:center;gap:8px;margin-bottom:16px;flex-wrap:wrap}
.q-num{color:#475569;font-size:0.75rem;font-family:monospace;font-weight:700}
.badge{font-size:0.65rem;font-weight:700;padding:3px 9px;border-radius:20px;letter-spacing:0.04em}
.badge-mc{background:#1e3a5f;color:#93c5fd}
.badge-tf{background:#1a3a2a;color:#86efac}
.badge-fitb{background:#3b2f1e;color:#fbbf24}
.badge-accuracy{background:#2e1b3d;color:#c084fc}
.badge-completeness{background:#2d2d1a;color:#fde68a}
.q-timing{margin-left:auto;color:#475569;font-size:0.78rem}
.prompt{font-size:1.2rem;font-weight:600;color:#f1f5f9;margin-bottom:28px;line-height:1.5}
.q-stats{margin-top:16px;font-size:0.78rem;color:#475569;text-align:right}
.q-img{display:block;max-width:100%;max-height:360px;border-radius:12px;margin:-12px 0 24px}
.opt-imgs{display:flex;flex-wrap:wrap;gap:12px;margin:-12px 0 24px}
.opt-fig{text-align:center}
.opt-img{display:block;max-height:120px;max-width:200px;border-radius:8px}
.opt-fig figcaption{font-size:0.75rem;color:#64748b;margin-top:4px}
.img-note{color:#64748b;font-size:0.85rem;margin:-12px 0 24px}
.pct-correct{color:#4ade80;font-weight:700}

/* ── Bar chart ── */
.bar-chart{display:flex;flex-direction:column;gap:10px}
.bar-row{display:flex;align-items:center;gap:12px}
.bar-label{
  font-size:0.88rem;font-weight:500;color:#cbd5e1;
  width:210px;flex-shrink:0;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.bar-track{flex:1;height:44px;background:#0f172a;border-radius:8px;overflow:hidden}
.bar-fill{height:100%;border-radius:8px;display:flex;align-items:center;justify-content:flex-end;padding-right:12px}
.bar-count{font-size:0.85rem;font-weight:700;color:#fff}
.bar-correct-mark{font-size:0.72rem;color:#4ade80;font-weight:700;width:68px;flex-shrink:0}

/* ── Word cloud ── */
.wc-answer-note{text-align:center;color:#64748b;font-size:0.88rem;margin-bottom:14px}
.wc-answer{color:#4ade80;font-weight:700}
.word-cloud{
  display:flex;flex-wrap:wrap;gap:10px 18px;
  justify-content:center;align-items:center;
  min-height:80px;padding:20px;
  background:#0f172a;border-radius:12px;
}
.wc-word{font-weight:700;color:#64748b}
.wc-word.correct{color:#4ade80}
.wc-empty{color:#334155;font-size:0.9rem;text-align:center;padding:20px}

/* ── Histogram ── */
.summary-card{
  background:#1e293b;border:1px solid #334155;border-radius:16px;
  padding:36px;margin-bottom:24px;
}
.summary-card h2{font-size:1.4rem;font-weight:800;color:#f1f5f9;margin-bottom:32px;text-align:center}
.histogram{display:flex;align-items:flex-end;gap:8px;margin-bottom:0}
.hist-col{flex:1;display:flex;flex-direction:column;align-items:center}
.hist-count{font-size:0.72rem;font-weight:700;color:#94a3b8;margin-bottom:4px;min-height:18px}
.hist-bar{width:100%;background:#6366f1;border-radius:4px 4px 0 0}
.hist-labels{display:flex;gap:8px;margin-top:6px}
.hist-label{flex:1;text-align:center;font-size:0.68rem;color:#475569}
.hist-axis{text-align:center;font-size:0.7rem;color:#334155;margin-top:6px}

/* ── Summary stats ── */
.stats-row{
  display:flex;justify-content:center;
  margin-top:32px;padding-top:28px;border-top:1px solid #334155;
}
.stat-box{flex:1;text-align:center;padding:0 16px}
.stat-box+.stat-box{border-left:1px solid #334155}
.stat-label{font-size:0.65rem;text-transform:uppercase;letter-spacing:0.12em;color:#64748b;margin-bottom:6px}
.stat-value{font-size:2rem;font-weight:900;color:#f1f5f9}

/* ── Footer ── */
.rpt-footer{text-align:center;margin-top:56px;color:#1e293b;font-size:0.72rem}
"""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def build_session_report(db: AsyncSession, session_id: str) -> tuple[str, bytes]:
    """
    Return (filename, html_bytes) for a standalone session report.
    Aggregate statistics only — no player names or individual scores.
    """
    session = await db.get(GameSession, session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found")

    game = await db.get(Game, session.game_id)
    course = await db.get(Course, session.course_id)

    # Questions as they were asked (snapshot). Questions an abandoned game never
    # reached are left out of the report.
    from .game_service import load_snapshot

    questions = [q for q in await load_snapshot(db, session) if q.asked_at is not None]
    images = _ImageEmbedder(*await _load_report_images(db, questions))

    s_result = await db.execute(
        select(SessionScore).where(SessionScore.session_id == session_id)
    )
    all_scores = s_result.scalars().all()

    # Group by question
    by_question: dict[int, list[SessionScore]] = defaultdict(list)
    for s in all_scores:
        by_question[s.question_id].append(s)

    # Unique players and totals for histogram
    player_totals: dict[str, float] = defaultdict(float)
    for s in all_scores:
        player_totals[s.user_id] += float(s.points_awarded)
    final_scores = list(player_totals.values())
    total_players = len(player_totals)

    max_possible = sum(q.points_value for q in questions)
    game_title = game.title if game else "Unknown Game"
    course_name = f"{course.name} — {course.semester}" if course else "Unknown Course"

    ts = session.completed_at or session.created_at
    date_played = ts.strftime("%B %d, %Y") if ts else "Unknown Date"

    # ── Question sections ──────────────────────────────────────────────────
    q_sections: list[str] = []
    for idx, q in enumerate(questions):
        q_scores = by_question.get(q.question_id, [])
        answered = len(q_scores)
        correct_count = sum(1 for s in q_scores if s.is_correct)

        dist: dict[str, int] = defaultdict(int)
        for s in q_scores:
            key = _extract_answer_key(q.type, s.answer_data)
            if key is not None:
                dist[key] += 1

        reveal = _answer_reveal(q)
        is_accuracy = q.grading_type == "ACCURACY"

        type_badge = {
            "multiple_choice": ("Multiple Choice", "badge-mc"),
            "true_false": ("True / False", "badge-tf"),
            "fill_in_the_blank": ("Fill in the Blank", "badge-fitb"),
            "pin_drop": ("Pin Drop", ""),
            "closest_guess": ("Closest Guess", ""),
        }.get(q.type, (q.type, ""))
        type_label, type_class = type_badge

        grading_label = "Accuracy" if is_accuracy else "Completeness"
        grading_class = "badge-accuracy" if is_accuracy else "badge-completeness"

        pts_val = f"{q.points_value:g}"
        pts_label = f"{pts_val} pt{'s' if q.points_value != 1 else ''}"

        if q.type in ("multiple_choice", "true_false"):
            chart = _render_bar_chart(q, dict(dist), reveal, total_players)
        elif q.type == "fill_in_the_blank":
            chart = _render_word_cloud(q, dict(dist), reveal)
        elif q.type in NEW_TYPES:
            chart = _new_type_summary(q, q_scores)
        else:
            chart = ""

        if is_accuracy and answered > 0:
            pct = round(correct_count / answered * 100)
            stats = (
                f'<div class="q-stats">'
                f"{answered} / {total_players} answered"
                f' · <span class="pct-correct">{pct}% correct</span>'
                f"</div>"
            )
        else:
            stats = f'<div class="q-stats">{answered} / {total_players} answered</div>'

        # Prompt was already bleach-sanitised on write; embed as HTML
        q_sections.append(
            f'<div class="q-card">'
            f'<div class="q-meta">'
            f'<span class="q-num">Q{idx + 1} of {len(questions)}</span>'
            f'<span class="badge {type_class}">{_esc(type_label)}</span>'
            f'<span class="badge {grading_class}">{_esc(grading_label)}</span>'
            f'<span class="q-timing">{q.time_limit_seconds}s · {_esc(pts_label)}</span>'
            f"</div>"
            f'<p class="prompt">{q.prompt}</p>'
            f"{images.question_images(q)}"
            f"{chart}"
            f"{stats}"
            f"</div>"
        )

    # ── Histogram ─────────────────────────────────────────────────────────
    buckets = _build_histogram(final_scores, max_possible)
    histogram = _render_histogram(buckets)
    avg = round(sum(final_scores) / len(final_scores), 1) if final_scores else 0
    high = max(final_scores) if final_scores else 0

    # ── Assemble document ─────────────────────────────────────────────────
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    safe = re.sub(r"[^a-zA-Z0-9-]", "_", game_title)  # spaces and special chars → _
    safe = re.sub(r"_+", "_", safe).strip("_")  # collapse runs, trim edges
    date_str = ts.strftime("%Y-%m-%d") if ts else "unknown"
    filename = f"report_{safe}_{date_str}_{session_id[:8]}.html"

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=1024">
<title>{_esc(game_title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="container">

<div class="rpt-header">
  <h1>{_esc(game_title)}</h1>
  <div class="rpt-meta">
    <span>{_esc(course_name)}</span>
    <span class="sep">·</span>
    <span>{_esc(date_played)}</span>
    <span class="sep">·</span>
    <span>{total_players} player{"s" if total_players != 1 else ""}</span>
    <span class="sep">·</span>
    <span>Max {max_possible:g} pts</span>
  </div>
</div>

<p class="section-label">Questions &amp; Results</p>

{"".join(q_sections)}

<div class="summary-card">
  <h2>Score Distribution</h2>
  {histogram}
  <div class="stats-row">
    <div class="stat-box">
      <div class="stat-label">Players</div>
      <div class="stat-value">{total_players}</div>
    </div>
    <div class="stat-box">
      <div class="stat-label">Average Score</div>
      <div class="stat-value">{avg:g}</div>
    </div>
    <div class="stat-box">
      <div class="stat-label">High Score</div>
      <div class="stat-value">{high:g}</div>
    </div>
    <div class="stat-box">
      <div class="stat-label">Max Possible</div>
      <div class="stat-value">{max_possible:g}</div>
    </div>
  </div>
</div>

<div class="rpt-footer">Generated by Buzzer · {_esc(timestamp)}</div>

</div>
</body>
</html>"""

    return filename, doc.encode()
