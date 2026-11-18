"""Build the report's tables and figures from eval/results/ ONLY (CLAUDE.md section 16, M8).

    .venv\\Scripts\\python.exe -m scripts.make_report
    .venv\\Scripts\\python.exe -m scripts.make_report --answers 20261113-101102_answers

By default it picks the latest valid run of each kind:
- answers:     the newest *_answers run whose metrics.json says "complete": true;
- retrieval:   the newest *_retrieval run not listed in EXCLUDED_RUNS;
- calibration: the newest *_calibration run made from that answers run.

It writes
    docs/report/RESULTS.md       tables (regenerated every time; never edit by hand)
    docs/report/figures/*.svg    plain SVG charts, written as text (no plotting library)

Every number comes from a metrics.json, per_question.csv, config.json or model_ids.json in
the chosen run folders. The only arithmetic done here is formatting, the "target met?"
comparison, and the chance agreement behind Cohen's kappa (from the saved confusion
matrix). No LLM calls, no index access.
"""

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from app.config import PROJECT_ROOT

RESULTS_DIR = PROJECT_ROOT / "eval" / "results"
REPORT_DIR = PROJECT_ROOT / "docs" / "report"

# Runs that must never be used in the report, with the reason (see docs/NOTES.md).
EXCLUDED_RUNS = {
    "20261107-125802_retrieval": "flawed superseded-ordering metric (NOTES.md, M3)",
}

RETRIEVAL_MODES = ("dense", "bm25", "hybrid_raw", "hybrid")
MODE_LABELS = {"dense": "dense", "bm25": "BM25", "hybrid_raw": "RRF only",
               "hybrid": "hybrid (system)"}
SYSTEM_LABELS = {"full": "Full system", "baseline": "Baseline"}
CAL_LABELS = ("correct", "partially_correct", "incorrect")


# --------------------------------------------------------------------------- run selection

def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def latest_run(results: Path, kind: str, accept=lambda run, metrics: True) -> Path:
    """Newest `<timestamp>_<kind>` folder with a metrics.json that `accept` approves."""
    for run in sorted(results.glob(f"*_{kind}"), reverse=True):  # timestamps sort as text
        if run.name in EXCLUDED_RUNS or not (run / "metrics.json").exists():
            continue
        if accept(run, read_json(run / "metrics.json")):
            return run
    raise SystemExit(f"No usable *_{kind} run in {results}.")


@dataclass
class Runs:
    answers: Path
    retrieval: Path
    calibration: Path | None


def choose_runs(results: Path, answers: str | None = None, retrieval: str | None = None,
                calibration: str | None = None) -> Runs:
    """Runs named on the command line, or the latest valid ones."""
    for name in (answers, retrieval, calibration):
        if name in EXCLUDED_RUNS:
            raise SystemExit(f"{name} is excluded: {EXCLUDED_RUNS[name]}")
    a = results / answers if answers else latest_run(
        results, "answers", lambda run, m: m.get("complete") is True)
    r = results / retrieval if retrieval else latest_run(results, "retrieval")
    if calibration:
        c: Path | None = results / calibration
    else:
        try:
            c = latest_run(results, "calibration", lambda run, m: m.get("answers_run") == a.name)
        except SystemExit:
            c = None  # calibration not done yet: the report says so
    return Runs(a, r, c)


# --------------------------------------------------------------------------- formatting

def f3(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def secs(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1f} s"


def md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def target_status(value: float | None, target: float) -> str:
    """'met' when the measured p95 is at or under the target."""
    if value is None:
        return "n/a"
    return "met" if value <= target else "**not met**"


# --------------------------------------------------------------------------- SVG charts

@dataclass
class Series:
    name: str
    values: list[float | None]
    slot: int              # categorical colour slot, 1-4, assigned in fixed order
    labelled: bool = True  # print the value at the end of each bar


# Validated categorical palette (dataviz reference palette, slots 1-4), light and dark.
SVG_STYLE = """
.bg{fill:#fcfcfb} .ink{fill:#0b0b0b} .ink2{fill:#52514e} .muted{fill:#898781}
.grid{stroke:#e1e0d9} .axis{stroke:#c3c2b7} .target{stroke:#0b0b0b}
.s1{fill:#2a78d6} .s2{fill:#eb6834} .s3{fill:#1baf7a} .s4{fill:#eda100}
.hit{fill:transparent} .hit:hover{fill:rgba(11,11,11,0.05)}
text{font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
@media (prefers-color-scheme: dark){
 .bg{fill:#1a1a19} .ink{fill:#f0efec} .ink2{fill:#c3c2b7}
 .grid{stroke:#2c2c2a} .axis{stroke:#383835} .target{stroke:#f0efec}
 .s1{fill:#3987e5} .s2{fill:#d95926} .s3{fill:#199e70} .s4{fill:#c98500}
 .hit:hover{fill:rgba(255,255,255,0.06)}
}
"""


def nice_max(value: float) -> tuple[float, float]:
    """A round axis maximum at or above `value`, and its tick step (at most 5 ticks)."""
    for step in (0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 20, 25, 50, 100, 200, 500):
        top = step * -(-value // step)  # ceil to a multiple of step
        if top / step <= 5:
            return max(top, step), step
    return value, value / 5


def bar_path(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """Horizontal bar: square at the baseline, rounded at the data end."""
    r = min(r, w / 2, h / 2)
    if w <= 0:
        return ""
    return (f"M{x:.1f},{y:.1f} H{x + w - r:.1f} A{r},{r} 0 0 1 {x + w:.1f},{y + r:.1f} "
            f"V{y + h - r:.1f} A{r},{r} 0 0 1 {x + w - r:.1f},{y + h:.1f} H{x:.1f} Z")


def bar_chart_svg(title: str, subtitle: str, categories: list[str], series: list[Series],
                  x_max: float, fmt, targets: dict[int, tuple[float, str]] | None = None,
                  tick_fmt=None) -> str:
    """Horizontal grouped bar chart as a standalone SVG string.

    categories: one row group each; series: one bar per category (legend at the top).
    fmt formats bar values (labels and hover); tick_fmt the axis ticks (default: fmt).
    targets: {category index: (value, label)} drawn as a dashed line across that group.
    Hovering a bar shows its exact value (native SVG <title> tooltip).
    """
    targets = targets or {}
    tick_fmt = tick_fmt or fmt
    width, left, right = 760, 210, 70
    plot_w = width - left - right
    bar_h, bar_gap, group_gap, target_pad = 14, 2, 18, 14
    top = 96
    axis_max, step = nice_max(x_max)

    def x_of(v: float) -> float:
        return left + plot_w * min(v, axis_max) / axis_max

    group_h = len(series) * bar_h + (len(series) - 1) * bar_gap
    rows, y = [], top
    for i in range(len(categories)):
        y += target_pad if i in targets else 0
        rows.append(y)
        y += group_h + group_gap
    plot_bottom = y - group_gap + 6
    height = int(plot_bottom + 40)

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
           f"<title>{escape(title)}</title>", f"<style>{SVG_STYLE}</style>",
           f'<rect class="bg" width="{width}" height="{height}" rx="8"/>',
           f'<text class="ink" x="16" y="28" font-size="16" font-weight="600">{escape(title)}</text>',
           f'<text class="ink2" x="16" y="48" font-size="12">{escape(subtitle)}</text>']

    # Legend (always present for two or more series).
    lx = 16
    for s in series:
        out.append(f'<rect class="s{s.slot}" x="{lx}" y="62" width="12" height="12" rx="3"/>')
        out.append(f'<text class="ink2" x="{lx + 18}" y="72" font-size="12">{escape(s.name)}</text>')
        lx += 18 + 7.2 * len(s.name) + 24

    # Recessive gridlines and tick labels.
    tick = 0.0
    while tick <= axis_max + 1e-9:
        gx = x_of(tick)
        out.append(f'<line class="{"axis" if tick == 0 else "grid"}" x1="{gx:.1f}" y1="{top - 8}" '
                   f'x2="{gx:.1f}" y2="{plot_bottom:.1f}" stroke-width="1"/>')
        out.append(f'<text class="muted" x="{gx:.1f}" y="{plot_bottom + 16:.1f}" font-size="11" '
                   f'text-anchor="middle">{escape(tick_fmt(tick))}</text>')
        tick = round(tick + step, 6)

    for i, (category, gy) in enumerate(zip(categories, rows)):
        out.append(f'<text class="ink" x="{left - 12}" y="{gy + group_h / 2 + 4:.1f}" '
                   f'font-size="12" text-anchor="end">{escape(category)}</text>')
        for j, s in enumerate(series):
            by = gy + j * (bar_h + bar_gap)
            value = s.values[i]
            shown = "n/a" if value is None else fmt(value)
            out.append("<g>")
            out.append(f"<title>{escape(f'{category} - {s.name}: {shown}')}</title>")
            out.append(f'<rect class="hit" x="{left}" y="{by - 1}" width="{plot_w}" '
                       f'height="{bar_h + 2}"/>')
            if value is not None:
                out.append(f'<path class="s{s.slot}" d="{bar_path(left, by, x_of(value) - left, bar_h)}"/>')
            if s.labelled or value is None:
                end = x_of(value or 0)
                if i in targets:  # keep the label clear of the dashed target line
                    tx = x_of(targets[i][0])
                    end = max(end, tx + 2) if end < tx + 40 else end
                lx = end + 6
                out.append(f'<text class="{"ink2" if value is not None else "muted"}" x="{lx:.1f}" '
                           f'y="{by + bar_h - 3}" font-size="11">{escape(shown)}</text>')
            out.append("</g>")
        if i in targets:
            value, label = targets[i]
            tx = x_of(value)
            out.append(f'<line class="target" x1="{tx:.1f}" y1="{gy - 6}" x2="{tx:.1f}" '
                       f'y2="{gy + group_h + 3}" stroke-width="1.5" stroke-dasharray="4 3"/>')
            out.append(f'<text class="ink" x="{tx + 4:.1f}" y="{gy - 4}" font-size="11">'
                       f'{escape(label)}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- integrity check

def integrity_issues(answers_dir: Path) -> list[str]:
    """Differences between answers.jsonl and the run's own per_question.csv.

    Both files are written by the same run, so any difference means a file was edited
    afterwards. The report's numbers come from metrics.json and per_question.csv.
    """
    issues = []
    metrics = read_json(answers_dir / "metrics.json")
    if metrics.get("complete") is not True:
        issues.append("metrics.json does not say the run is complete.")
    csv_labels = {(r["id"], r["system"]): r["correctness"]
                  for r in read_csv(answers_dir / "per_question.csv")}
    path = answers_dir / "answers.jsonl"
    if not path.exists():
        return issues
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        a = json.loads(line)
        judged = (a.get("judge") or {}).get("correctness", "")
        expected = csv_labels.get((a["id"], a["system"]))
        if expected is not None and judged != expected:
            issues.append(f"answers.jsonl {a['id']} ({a['system']}): judge correctness "
                          f"'{judged}' but per_question.csv has '{expected}'.")
    return issues


# --------------------------------------------------------------------------- report sections

def evaluation_set_section(am: dict) -> str:
    cats = am["systems"]["full"]["by_category"]
    rows = [[c, str(cats[c]["questions"])] for c in cats]
    return "\n\n".join([
        "## 1. Evaluation set",
        f"- Questions: **{am['n_questions']}**; reviewed by the author: "
        f"**{am['n_reviewed']} of {am['n_questions']}**.",
        md_table(["Category", "Questions"], rows),
    ])


def retrieval_section(rm: dict, run: Path, fig: str) -> str:
    modes = rm["by_mode"]
    rows = []
    for mode in RETRIEVAL_MODES:
        o = modes[mode]
        name = f"**{MODE_LABELS[mode]}**" if mode == "hybrid" else MODE_LABELS[mode]
        rows.append([name, f3(o["recall@5"]), f3(o["recall@10"]), f3(o["mrr"]), f3(o["ndcg@10"]),
                     f3(o["access_leak_rate"]), f3(o["cross_engagement_leak_rate"]),
                     f"{f3(o['superseded_order_accuracy'])} ({o['superseded_questions']} q)",
                     f"{o['latency_ms_p50']:.0f} / {o['latency_ms_p95']:.0f} ms"])
    dense, hybrid = modes["dense"]["per_source_type"], modes["hybrid"]["per_source_type"]
    src_rows = [[st, str(hybrid[st]["gold_docs"]), f3(dense[st]["recall@5"]), f3(hybrid[st]["recall@5"]),
                 f3(dense[st]["recall@10"]), f3(hybrid[st]["recall@10"])] for st in sorted(hybrid)]
    scored = modes["hybrid"]["questions_scored"]
    return "\n\n".join([
        "## 2. Retrieval",
        f"Run `{run.name}`: {rm['n_questions']} questions, {scored} with gold documents; "
        f"{rm['n_reviewed']} reviewed. Document-level metrics; no LLM calls.",
        md_table(["Mode", "recall@5", "recall@10", "MRR", "nDCG@10", "role leak",
                  "engagement leak", "superseded order", "latency p50 / p95"], rows),
        f"![Retrieval quality by mode](figures/{fig})",
        "Per source type (gold documents of that type found in the top k):",
        md_table(["Source type", "gold docs", "dense R@5", "hybrid R@5", "dense R@10",
                  "hybrid R@10"], src_rows),
    ])


def answers_section(am: dict, run: Path, fig_quality: str, fig_cat: str) -> str:
    full, base = am["systems"]["full"]["overall"], am["systems"]["baseline"]["overall"]
    metric_rows = [
        ("Correctness (correct = 1, partial = 0.5)", "correctness"),
        ("Fully correct answers", "correct_rate"),
        ("Faithfulness, micro (supported factual claims)", "faithfulness_micro"),
        ("Faithfulness, macro (mean per answer)", "faithfulness_macro"),
        ("Citation precision", "citation_precision"),
    ]
    rows = [[label, f3(full[key]), f3(base[key])] for label, key in metric_rows]
    rows.append(["Errors", str(full["errors"]), str(base["errors"])])

    fc, bc = am["systems"]["full"]["by_category"], am["systems"]["baseline"]["by_category"]
    cat_rows = [[c, str(fc[c]["questions"]), f3(fc[c]["correctness"]), f3(bc[c]["correctness"]),
                 f3(fc[c]["faithfulness_micro"]), f3(bc[c]["faithfulness_micro"])] for c in fc]

    def statuses(s: dict) -> str:
        return ", ".join(f"{k} {v}" for k, v in sorted(s["status_counts"].items()))

    return "\n\n".join([
        "## 3. Answer quality: full system against the single-pass baseline",
        f"Run `{run.name}` (complete: {am['complete']}). Judge: `{am['judge_model']}`. "
        "Both systems use the same models and the same access filter; the baseline has dense "
        "retrieval only, one synthesis call and no verification.",
        md_table(["Metric", SYSTEM_LABELS["full"], SYSTEM_LABELS["baseline"]], rows),
        f"![Answer quality, full system against baseline](figures/{fig_quality})",
        "By category (correctness, then micro faithfulness; n/a = no factual claims to judge):",
        md_table(["Category", "n", "full correct.", "baseline correct.", "full faithf.",
                  "baseline faithf."], cat_rows),
        f"![Correctness by question category](figures/{fig_cat})",
        f"Answer status: full system: {statuses(full)}; baseline: {statuses(base)}.",
    ])


def leak_section(am: dict, rm: dict) -> str:
    rows = []
    for system in ("full", "baseline"):
        o = am["systems"][system]["overall"]
        rows.append([SYSTEM_LABELS[system] + " (answers)", f3(o["access_leak_rate"]),
                     f3(o["cross_engagement_leak_rate"]), f3(o["content_leak_rate"])])
    for mode in RETRIEVAL_MODES:
        o = rm["by_mode"][mode]
        rows.append([f"Retrieval: {MODE_LABELS[mode]}", f3(o["access_leak_rate"]),
                     f3(o["cross_engagement_leak_rate"]), "n/a"])
    return "\n\n".join([
        "## 4. Access-control leaks (required: 0)",
        "Role and engagement leaks are checked against an access rule written separately from "
        "`app/retrieval/acl.py`; content leak = a listed forbidden fact appears in the answer "
        "to a denied question.",
        md_table(["Where", "role leak", "cross-engagement leak", "content leak"], rows),
    ])


def latency_section(am: dict, fig: str) -> str:
    t = am["targets"]
    rows = []
    for system in ("full", "baseline"):
        o = am["systems"][system]["overall"]
        rows.append([SYSTEM_LABELS[system], secs(o["ttft_s_p50"]), secs(o["ttft_s_p95"]),
                     target_status(o["ttft_s_p95"], t["ttft_p95_s"]),
                     secs(o["full_answer_s_p50"]), secs(o["full_answer_s_p95"]),
                     target_status(o["full_answer_s_p95"], t["full_answer_p95_s"])])
    return "\n\n".join([
        "## 5. Latency against the project document's targets",
        f"Targets: p95 time to first token < {t['ttft_p95_s']} s; p95 full answer "
        f"< {t['full_answer_p95_s']} s. Measured on the development laptop (CPU).",
        md_table(["System", "TTFT p50", "TTFT p95", f"TTFT target ({t['ttft_p95_s']} s)",
                  "full p50", "full p95", f"full target ({t['full_answer_p95_s']} s)"], rows),
        f"![Latency against targets](figures/{fig})",
    ])


def chance_agreement(confusion: dict) -> float:
    """Expected agreement by chance, from the human (rows) and judge (columns) totals."""
    n = sum(sum(row.values()) for row in confusion.values())
    human = {h: sum(confusion[h].values()) for h in confusion}
    judge = {j: sum(confusion[h].get(j, 0) for h in confusion) for j in CAL_LABELS}
    return sum(human.get(label, 0) * judge[label] for label in CAL_LABELS) / (n * n)


def calibration_section(cal_dir: Path | None, answers_dir: Path) -> str:
    head = "## 6. Judge calibration (author's hand labels against the LLM judge)"
    if cal_dir is None:
        return f"{head}\n\nNot run yet for `{answers_dir.name}`."
    cm = read_json(cal_dir / "metrics.json")
    conf = cm["confusion_human_rows_judge_cols"]
    rows = [[f"human: {h}"] + [str(conf[h][j]) for j in CAL_LABELS] for h in CAL_LABELS]
    categories = {(r["id"], r["system"]): r["category"]
                  for r in read_csv(answers_dir / "per_question.csv")}
    disagreements = [[r["sample"], r["id"], r["system"], categories.get((r["id"], r["system"]), ""),
                      r["human"], r["judge"]]
                     for r in read_csv(cal_dir / "per_question.csv") if r["agree"] != "True"]
    kappa = cm["cohens_kappa"]
    return "\n\n".join([
        head,
        f"Run `{cal_dir.name}`, from answers run `{cm['answers_run']}`: "
        f"{cm['labelled']} of {cm['exported']} exported answers labelled.",
        md_table(["Measure", "Value"], [
            ["Exact agreement (3 classes)", f3(cm["exact_agreement"])],
            ["Agreement expected by chance (from the confusion matrix)", f3(chance_agreement(conf))],
            ["Cohen's kappa", "undefined" if kappa is None else f3(kappa)],
            ["Binary agreement (correct vs not correct)", f3(cm["binary_agreement_correct_vs_not"])],
        ]),
        "Confusion matrix (rows = author, columns = judge):",
        md_table([""] + [f"judge: {j}" for j in CAL_LABELS], rows),
        "Disagreements:",
        md_table(["Sample", "Question", "System", "Category", "Author", "Judge"], disagreements)
        if disagreements else "None.",
    ])


def cost_and_models_section(am: dict, answers_dir: Path) -> str:
    models = read_json(answers_dir / "model_ids.json")
    config = read_json(answers_dir / "config.json")
    full, base = am["systems"]["full"]["overall"], am["systems"]["baseline"]["overall"]
    spend = am.get("measured_spend_usd")
    sessions = am.get("measured_spend_sessions", "n/a")
    model_rows = [[role, f"`{model}`"] for role, model in models.items()]
    effort = ", ".join(f"{k.removeprefix('effort_')} = {config[k]}"
                       for k in ("effort_light", "effort_synth", "effort_verify") if k in config)
    return "\n\n".join([
        "## 7. Cost and models",
        md_table(["Item", "USD"], [
            [f"Measured spend, answers + judge (OpenRouter balance before/after; "
             f"sessions measured: {sessions})", "n/a" if spend is None else f"{spend:.2f}"],
            ["Estimated answer cost, full system", f"{full['estimated_answer_cost_usd']:.2f}"],
            ["Estimated answer cost, baseline", f"{base['estimated_answer_cost_usd']:.2f}"],
            ["Estimated judge cost", f"{am['estimated_judge_cost_usd']:.2f}"],
        ]),
        md_table(["Role", "Model"], model_rows),
        f"Effort settings: {effort}. No sampling parameters are sent (removed for the 5.5 "
        "models), so answers vary between runs; run-to-run variance has not been measured.",
    ])


def integrity_section(issues: list[str]) -> str:
    body = ("No differences found between `answers.jsonl` and `per_question.csv`."
            if not issues else
            "The run's files disagree with each other, so a file was changed after the run. "
            "All numbers above come from `metrics.json` and `per_question.csv`, which are "
            "unchanged:\n\n" + "\n".join(f"- {i}" for i in issues))
    return f"## 8. Integrity check\n\n{body}"


# --------------------------------------------------------------------------- figures

def figures(am: dict, rm: dict) -> dict[str, str]:
    """Figure file name -> SVG text."""
    full, base = am["systems"]["full"]["overall"], am["systems"]["baseline"]["overall"]
    keys = [("Correctness", "correctness"), ("Fully correct", "correct_rate"),
            ("Faithfulness (micro)", "faithfulness_micro"), ("Citation precision", "citation_precision")]
    quality = bar_chart_svg(
        "Answer quality: full system vs single-pass baseline",
        f"{am['n_questions']} questions, judged by {am['judge_model']}; "
        f"{am['n_reviewed']} of {am['n_questions']} reviewed by the author",
        [k[0] for k in keys],
        [Series(SYSTEM_LABELS["full"], [full[k[1]] for k in keys], 1),
         Series(SYSTEM_LABELS["baseline"], [base[k[1]] for k in keys], 2)],
        1.0, f3, tick_fmt=lambda v: f"{v:.1f}")

    fc, bc = am["systems"]["full"]["by_category"], am["systems"]["baseline"]["by_category"]
    cats = list(fc)
    by_cat = bar_chart_svg(
        "Correctness by question category",
        "Correct = 1, partially correct = 0.5; small categories move a lot with one answer",
        [f"{c} (n={fc[c]['questions']})" for c in cats],
        [Series(SYSTEM_LABELS["full"], [fc[c]["correctness"] for c in cats], 1),
         Series(SYSTEM_LABELS["baseline"], [bc[c]["correctness"] for c in cats], 2)],
        1.0, f3, tick_fmt=lambda v: f"{v:.1f}")

    rkeys = [("recall@5", "recall@5"), ("MRR", "mrr"), ("nDCG@10", "ndcg@10"),
             ("Superseded order", "superseded_order_accuracy")]
    modes = rm["by_mode"]
    retrieval = bar_chart_svg(
        "Retrieval quality by mode",
        f"{modes['hybrid']['questions_scored']} questions with gold documents; "
        "value labels shown for the system (hybrid); all values in RESULTS.md",
        [k[0] for k in rkeys],
        [Series(MODE_LABELS[m], [modes[m][k[1]] for k in rkeys], slot, labelled=(m == "hybrid"))
         for slot, m in enumerate(RETRIEVAL_MODES, start=1)],
        1.0, f3, tick_fmt=lambda v: f"{v:.1f}")

    t = am["targets"]
    lkeys = [("First token p50", "ttft_s_p50"), ("First token p95", "ttft_s_p95"),
             ("Full answer p50", "full_answer_s_p50"), ("Full answer p95", "full_answer_s_p95")]
    values = {s: [am["systems"][s]["overall"][k[1]] for k in lkeys] for s in ("full", "baseline")}
    latency = bar_chart_svg(
        "Latency against the project document's targets",
        "Seconds, development laptop; dashed line = p95 target",
        [k[0] for k in lkeys],
        [Series(SYSTEM_LABELS["full"], values["full"], 1),
         Series(SYSTEM_LABELS["baseline"], values["baseline"], 2)],
        max(v for vs in values.values() for v in vs if v is not None),
        lambda v: f"{v:.1f} s" if v % 1 else f"{v:.0f} s",
        targets={1: (t["ttft_p95_s"], f"target {t['ttft_p95_s']} s"),
                 3: (t["full_answer_p95_s"], f"target {t['full_answer_p95_s']} s")})

    return {"answer_quality.svg": quality, "correctness_by_category.svg": by_cat,
            "retrieval_modes.svg": retrieval, "latency.svg": latency}


# --------------------------------------------------------------------------- main

def build_report(runs: Runs) -> tuple[str, dict[str, str]]:
    """Return (RESULTS.md text, {figure name: SVG text}) for the chosen runs."""
    am = read_json(runs.answers / "metrics.json")
    rm = read_json(runs.retrieval / "metrics.json")
    figs = figures(am, rm)
    sources = [runs.answers.name, runs.retrieval.name] + ([runs.calibration.name] if runs.calibration else [])
    header = "\n".join([
        "# Enterprise Fabric AI: evaluation results",
        "",
        "> Generated by `scripts/make_report.py` from `eval/results/` only. **Do not edit by "
        "hand**; re-run the script instead.",
        f"> Source runs: {', '.join(f'`{s}`' for s in sources)}.",
        f"> **{am['n_reviewed']} of {am['n_questions']} evaluation questions have been reviewed "
        "by the author; all figures are preliminary until they are.**",
    ])
    sections = [
        header,
        evaluation_set_section(am),
        retrieval_section(rm, runs.retrieval, "retrieval_modes.svg"),
        answers_section(am, runs.answers, "answer_quality.svg", "correctness_by_category.svg"),
        leak_section(am, rm),
        latency_section(am, "latency.svg"),
        calibration_section(runs.calibration, runs.answers),
        cost_and_models_section(am, runs.answers),
        integrity_section(integrity_issues(runs.answers)),
    ]
    return "\n\n".join(sections) + "\n", figs


def write_report(runs: Runs, out_dir: Path) -> Path:
    text, figs = build_report(runs)
    (out_dir / "figures").mkdir(parents=True, exist_ok=True)
    for name, svg in figs.items():
        (out_dir / "figures" / name).write_text(svg, encoding="utf-8")
    path = out_dir / "RESULTS.md"
    path.write_text(text, encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Report tables and SVG figures from eval/results/.")
    parser.add_argument("--answers", help="answers run folder (default: latest complete)")
    parser.add_argument("--retrieval", help="retrieval run folder (default: latest valid)")
    parser.add_argument("--calibration", help="calibration run folder (default: latest for the answers run)")
    args = parser.parse_args()
    runs = choose_runs(RESULTS_DIR, args.answers, args.retrieval, args.calibration)
    path = write_report(runs, REPORT_DIR)
    print(f"Wrote {path.relative_to(PROJECT_ROOT)} and 4 figures from "
          f"{runs.answers.name}, {runs.retrieval.name}, "
          f"{runs.calibration.name if runs.calibration else 'no calibration run'}.")
    for issue in integrity_issues(runs.answers):
        print("INTEGRITY:", issue)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
