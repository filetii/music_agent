"""Run the scenarios against the local model and write a markdown report.

    python -m evals.run [--model qwen3:8b] [--only id1,id2]
"""

import argparse
import json
import logging
import re
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from emporio.agent import create_session
from emporio.config import PROJECT_ROOT, load_settings

from .scenarios import SCENARIOS, Scenario

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
    return failures


def run(model: str | None, only: set[str] | None) -> Path:
    handoffs = Path(tempfile.mkdtemp()) / "handoffs.jsonl"
    settings = replace(load_settings(), handoffs_path=handoffs)
    if model:
        settings = replace(settings, ollama_model=model)
    rows, transcripts = [], []
    for scenario in SCENARIOS:
        if only and scenario.id not in only:
            continue
        session = create_session(settings, now=NOW)
        start, replies = time.perf_counter(), []
        for turn in scenario.turns:
            replies.append(session.send(turn))
        elapsed = time.perf_counter() - start
        failures = check(scenario, replies)
        tokens = sum(r.usage.get("input_tokens", 0) + r.usage.get("output_tokens", 0) for r in replies)
        tools = [t.name for r in replies for t in r.tool_calls]
        retries = sum(r.guard_retries for r in replies)
        rows.append((scenario, not failures, failures, tools, elapsed, tokens, retries))
        print(f"{'PASS' if not failures else 'FAIL'}  {scenario.id}  ({elapsed:.1f}s)  {'; '.join(failures)}", flush=True)

        transcripts.append(f"### {scenario.id} — {'PASS' if not failures else 'FAIL'}\n")
        for turn, reply in zip(scenario.turns, replies):
            transcripts.append(f"**Cliente:** {turn}\n")
            transcripts += [
                f"> tool: `{t.name}({json.dumps(t.arguments, ensure_ascii=False)})` -> {t.result['status']}\n"
                for t in reply.tool_calls
            ]
            transcripts.append(f"**Assistente:** {reply.text}\n")
        if failures:
            transcripts.append("Failures: " + "; ".join(f"`{f}`" for f in failures) + "\n")

    passed = sum(1 for r in rows if r[1])
    model = session.llm.name
    lines = [
        f"# Eval report — `{model}`",
        "",
        f"Run: {datetime.now():%Y-%m-%d %H:%M} · Scenarios: {len(rows)} · **Passed: {passed}/{len(rows)}**",
        f" · Mean latency per scenario: {sum(r[4] for r in rows) / len(rows):.1f}s"
        f" · Tokens (in+out): {sum(r[5] for r in rows):,}"
        f" · Guard retries: {sum(r[6] for r in rows)}",
        "",
        "| Scenario | Capability | Result | Tools called | Latency | Failures |",
        "|---|---|---|---|---|---|",
    ]
    for scenario, ok, failures, tools, elapsed, _, _ in rows:
        failure_text = "; ".join(f"`{f}`" for f in failures).replace("|", r"\|")
        lines.append(
            f"| {scenario.id} | {scenario.capability} | {'PASS' if ok else 'FAIL'} | "
            f"{', '.join(tools) or '—'} | {elapsed:.1f}s | {failure_text} |"
        )
    lines += ["", "## Transcripts", "", *transcripts]
    report = PROJECT_ROOT / "evals" / "reports" / f"{model.replace(':', '_').replace('/', '_')}.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n{passed}/{len(rows)} passed — report: {report}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="Ollama model to evaluate (default: OLLAMA_MODEL)")
    parser.add_argument("--only", help="comma-separated scenario ids")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.ERROR)
    run(args.model, set(args.only.split(",")) if args.only else None)


if __name__ == "__main__":
    main()
