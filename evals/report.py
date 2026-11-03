"""Markdown reports from the results database.

    python -m evals.report    # regenerate every report from evals/results.db

One report per (model, suite) for its latest finished run, plus benchmark.md comparing models.
"""

import json
import math
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from . import results
from .scenarios import SUITES

REPORTS_DIR = Path(__file__).parent / "reports"
CAPABILITIES = {s.id: s.capability for scenarios in SUITES.values() for s in scenarios}
METHOD_NOTE = (
    "Every scenario runs in a fresh session; the model samples at temperature 0.2 with no fixed seed, so "
    "repetitions can differ. **Pass rate** counts all trials; its 95% Wilson interval treats trials as "
    "independent, which is optimistic because repetitions of one scenario are correlated. **pass^k** is the "
    "chance that a scenario passes k repetitions in a row (averaged over scenarios): the consistency a "
    "customer would see. The agent was built and tuned on `qwen3:8b` with the core suite; the hard suite "
    "was written afterwards and never used to change the agent."
)


def wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """Confidence interval for a pass rate; unlike p ± z·se it stays inside [0, 1] near 100%."""
    p = successes / trials
    denominator = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denominator
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def pass_hat_k(counts: list[tuple[int, int]], k: int) -> float:
    """pass^k (tau-bench): for each scenario with c passes in n tries, C(c, k) / C(n, k); then the mean."""
    return sum(math.comb(c, k) / math.comb(n, k) for c, n in counts) / len(counts)


def summarize(rows: list[sqlite3.Row], k: int) -> dict:
    by_scenario = defaultdict(list)
    for row in rows:
        by_scenario[row["scenario_id"]].append(row)
    passes = sum(row["passed"] for row in rows)
    return {
        "by_scenario": by_scenario,
        "trials": len(rows),
        "passes": passes,
        "ci": wilson_interval(passes, len(rows)),
        "pass_hat_k": pass_hat_k([(sum(r["passed"] for r in v), len(v)) for v in by_scenario.values()], k),
        "latency": statistics.median(row["latency_s"] for row in rows),  # one runaway generation skews a mean
        "tokens": sum(row["tokens"] for row in rows) / len(rows),
        "retries": sum(row["guard_retries"] for row in rows),
    }


def run_report(run: sqlite3.Row, rows: list[sqlite3.Row]) -> str:
    k = run["repetitions"]
    s = summarize(rows, k)
    low, high = s["ci"]
    lines = [
        f"# Eval report — `{run['model']}`, {run['suite']} suite",
        "",
        f"Run {run['started_at'].replace('T', ' ')[:16]} · code `{run['git_commit']}` · "
        f"{len(s['by_scenario'])} scenarios × {k} repetitions = {s['trials']} trials",
        "",
        f"- **Pass rate: {s['passes']}/{s['trials']} ({s['passes'] / s['trials']:.0%})**, "
        f"95% CI {low:.0%}–{high:.0%}",
        f"- pass^{k} (passes all {k} repetitions): {s['pass_hat_k']:.0%}",
        f"- Per trial: {s['latency']:.1f}s median latency, {s['tokens']:,.0f} tokens on average · guard retries: {s['retries']}",
        "",
        METHOD_NOTE,
        "",
        "| Scenario | Capability | Passed | Median latency | Failures (count) |",
        "|---|---|---|---|---|",
    ]
    transcripts = []
    for scenario_id, attempts in s["by_scenario"].items():
        failures = Counter(f for a in attempts for f in json.loads(a["failures"]))
        failure_text = "; ".join(f"`{f}` ({n})" for f, n in failures.most_common()).replace("|", r"\|")
        latency = statistics.median(a["latency_s"] for a in attempts)
        lines.append(
            f"| {scenario_id} | {CAPABILITIES.get(scenario_id, '')} | {sum(a['passed'] for a in attempts)}/"
            f"{len(attempts)} | {latency:.1f}s | {failure_text} |"
        )
        shown = next((a for a in attempts if not a["passed"]), attempts[0])  # a failure if there is one
        result = "PASS" if shown["passed"] else "FAIL"
        transcripts.append(f"### {scenario_id} — repetition {shown['repetition']}, {result}\n\n{shown['transcript']}")
        if not shown["passed"]:
            transcripts.append("Failures: " + "; ".join(f"`{f}`" for f in json.loads(shown["failures"])) + "\n")
    lines += ["", "## Transcripts", "", "One per scenario: its first failing repetition, if any.", "", *transcripts]
    return "\n".join(lines)


def benchmark_report(conn: sqlite3.Connection) -> str:
    lines = ["# Benchmark — latest run of each model", "", METHOD_NOTE]
    runs = results.latest_runs(conn)
    for suite, scenarios in SUITES.items():
        summaries = [
            (run, summarize(results.run_results(conn, run["id"]), run["repetitions"]))
            for run in runs if run["suite"] == suite
        ]
        if not summaries:
            continue
        summaries.sort(key=lambda pair: pair[1]["passes"] / pair[1]["trials"], reverse=True)
        lines += [
            "", f"## {suite.capitalize()} suite ({len(scenarios)} scenarios)", "",
            "| Model | Pass rate | 95% CI | pass^k | Median latency | Mean tokens | Guard retries |",
            "|---|---|---|---|---|---|---|",
        ]
        for run, s in summaries:
            low, high = s["ci"]
            report = f"{suite}-{slug(run['model'])}.md"
            lines.append(
                f"| [`{run['model']}`]({report}) | {s['passes']}/{s['trials']} ({s['passes'] / s['trials']:.0%}) | "
                f"{low:.0%}–{high:.0%} | pass^{run['repetitions']} = {s['pass_hat_k']:.0%} | "
                f"{s['latency']:.1f}s | {s['tokens']:,.0f} | {s['retries']} |"
            )
        lines += [
            "", "Passes per scenario:", "",
            "| Scenario | " + " | ".join(f"`{run['model']}`" for run, _ in summaries) + " |",
            "|---|" + "---|" * len(summaries),
        ]
        for scenario in scenarios:
            cells = []
            for _, s in summaries:
                attempts = s["by_scenario"].get(scenario.id, [])
                cells.append(f"{sum(a['passed'] for a in attempts)}/{len(attempts)}" if attempts else "—")
            lines.append(f"| {scenario.id} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_reports(conn: sqlite3.Connection) -> list[Path]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for run in results.latest_runs(conn):
        if run["suite"] not in SUITES:
            continue  # ad-hoc runs (--only) stay in the database only
        path = REPORTS_DIR / f"{run['suite']}-{slug(run['model'])}.md"
        path.write_text(run_report(run, results.run_results(conn, run["id"])), encoding="utf-8")
        paths.append(path)
    path = REPORTS_DIR / "benchmark.md"
    path.write_text(benchmark_report(conn), encoding="utf-8")
    return [*paths, path]


def slug(model: str) -> str:
    return model.replace(":", "_").replace("/", "_")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    for path in write_reports(results.connect()):
        print(path)


if __name__ == "__main__":
    main()
