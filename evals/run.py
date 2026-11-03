"""Run the scenarios against local models, store every result in SQLite and write reports.

    python -m evals.run                                      # core suite, once, with OLLAMA_MODEL
    python -m evals.run --suite all --runs 5                 # core + hard suites, 5 repetitions
    python -m evals.run --models qwen3:8b,qwen3:4b+think --runs 5  # compare models (+think: thinking on)
    python -m evals.run --only price_takamine_gd20 --runs 3  # quick ad-hoc check
    python -m evals.run --regrade                            # re-apply edited checks to stored results
"""

import argparse
import json
import logging
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from emporio.agent import Reply, ToolTrace, create_session
from emporio.config import PROJECT_ROOT, Settings, load_settings
from emporio.llm import create_client
from emporio.llm.base import LLMClient

from . import results
from .report import write_reports
from .scenarios import SUITES, Scenario

NOW = datetime(2026, 3, 25, 10, 0)  # a Wednesday morning: store open, deterministic prompt


def check(scenario: Scenario, replies) -> list[str]:
    failures = []
    called = {t.name for r in replies for t in r.tool_calls}
    failures += [
        f"tool not called: {t}" for t in scenario.expect_tools if not any(alt in called for alt in t.split("|"))
    ]
    failures += [f"forbidden tool called: {t}" for t in scenario.forbid_tools if t in called]
    final = replies[-1].text
    failures += [f"missing /{p}/" for p in scenario.must_match if not re.search(p, final, re.IGNORECASE)]
    failures += [
        f"forbidden /{p}/" for p in scenario.must_not_match
        for r in replies if re.search(p, r.text, re.IGNORECASE)
    ]
    failures += [
        f"forbidden in final reply /{p}/" for p in scenario.final_must_not_match if re.search(p, final, re.IGNORECASE)
    ]
    return failures


def regrade(conn) -> int:
    """Re-apply the current checks to every stored result without calling the model again (e.g.
    after fixing a check that rejected a correct answer). Replies are read back from the stored
    transcripts; the checks only need the reply texts and the set of tools called."""
    scenarios = {s.id: s for suite in SUITES.values() for s in suite}
    changed = 0
    for row in conn.execute("SELECT * FROM results").fetchall():
        transcript = row["transcript"].split("\nFailures: ")[0]  # older rows carried their failures inline
        replies = [Reply(text) for text in _STORED_REPLY.findall(transcript)]
        replies[-1].tool_calls = [ToolTrace(name, {}, {}) for name in json.loads(row["tools"])]
        failures = check(scenarios[row["scenario_id"]], replies)
        if failures != json.loads(row["failures"]) or transcript != row["transcript"]:
            results.update_grade(conn, row["run_id"], row["scenario_id"], row["repetition"], failures, transcript)
            changed += 1
    return changed


_STORED_REPLY = re.compile(r"\*\*Assistente:\*\* (.*?)\n(?=\n\*\*Cliente:\*\* |\Z)", re.S)  # see transcript()


def run_suite(conn, llm: LLMClient, settings: Settings, suite: str, repetitions: int, only: set[str] | None) -> None:
    scenarios = [s for s in SUITES[suite] if not only or s.id in only]
    if not scenarios:
        return
    run_id = results.start_run(conn, llm.name, suite if not only else "adhoc", repetitions, git_commit())
    print(f"\n== {llm.name} · {suite} suite · {len(scenarios)} scenarios × {repetitions}", flush=True)
    for repetition in range(1, repetitions + 1):
        for scenario in scenarios:
            session = create_session(settings, llm=llm, now=NOW)
            start, replies = time.perf_counter(), []
            for turn in scenario.turns:
                replies.append(session.send(turn))
            elapsed = time.perf_counter() - start
            failures = check(scenario, replies)
            tokens = sum(r.usage.get("input_tokens", 0) + r.usage.get("output_tokens", 0) for r in replies)
            results.save_result(
                conn, run_id, scenario.id, repetition, failures, [t.name for r in replies for t in r.tool_calls],
                elapsed, tokens, sum(r.guard_retries for r in replies), transcript(scenario, replies),
            )
            print(f"{'PASS' if not failures else 'FAIL'}  #{repetition} {scenario.id}  ({elapsed:.1f}s)  "
                  f"{'; '.join(failures)}", flush=True)
    results.finish_run(conn, run_id)
    rows = results.run_results(conn, run_id)
    print(f"{llm.name} · {suite}: {sum(r['passed'] for r in rows)}/{len(rows)} passed", flush=True)


def transcript(scenario: Scenario, replies) -> str:
    lines = []
    for turn, reply in zip(scenario.turns, replies):
        lines.append(f"**Cliente:** {turn}\n")
        lines += [
            f"> tool: `{t.name}({json.dumps(t.arguments, ensure_ascii=False)})` -> {t.result['status']}\n"
            for t in reply.tool_calls
        ]
        lines.append(f"**Assistente:** {reply.text}\n")
    return "\n".join(lines)


def git_commit() -> str | None:
    """The code version being evaluated ("-dirty" if it has uncommitted changes)."""
    try:
        return subprocess.run(
            ["git", "describe", "--always", "--dirty"], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", help="comma-separated Ollama models to compare (default: OLLAMA_MODEL)")
    parser.add_argument("--suite", choices=[*SUITES, "all"], default="core")
    parser.add_argument("--runs", type=int, default=1, help="repetitions of each scenario (default: 1)")
    parser.add_argument("--only", help="comma-separated scenario ids (stored as an ad-hoc run, not reported)")
    parser.add_argument("--regrade", action="store_true", help="only re-apply the current checks to stored results")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.ERROR)

    if args.regrade:
        conn = results.connect()
        print(f"{regrade(conn)} results changed")
        print("Reports:", *write_reports(conn), sep="\n  ")
        return

    # Handoffs from the scenarios go to a throwaway database, not the real one
    settings = replace(load_settings(), handoffs_path=Path(tempfile.mkdtemp()) / "handoffs.db")
    models = args.models.split(",") if args.models else [settings.ollama_model]
    only = set(args.only.split(",")) if args.only else None
    conn = results.connect()
    for model in models:  # "qwen3:4b+think" runs the model with thinking on
        model_settings = replace(settings, ollama_model=model.removesuffix("+think"), ollama_think=model.endswith("+think"))
        for suite in SUITES if args.suite == "all" else [args.suite]:
            run_suite(conn, create_client(model_settings), model_settings, suite, args.runs, only)
    print("\nReports:", *write_reports(conn), sep="\n  ")


if __name__ == "__main__":
    main()
