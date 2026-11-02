"""Terminal chat with the agent.

    emporio [--provider anthropic|ollama] [--debug]

Commands: /save [file] exports the conversation (with tool calls) to markdown,
/reset starts a new conversation, /debug toggles tool-call display, /sair exits.
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from .agent import Reply, Session, create_session
from .config import PROJECT_ROOT, Settings, load_settings


def format_tool_call(trace) -> str:
    args = json.dumps(trace.arguments, ensure_ascii=False)
    return f"{trace.name}({args}) -> {trace.result['status']}"


def save_transcript(path: Path, settings: Settings, session: Session, turns: list[tuple[str, Reply]]) -> None:
    lines = [
        "# Conversa — Empório da Música",
        "",
        f"- Model: `{session.llm.name}`",
        f"- Reference date: {settings.reference_date:%d/%m/%Y}",
        f"- Saved: {datetime.now():%Y-%m-%d %H:%M}",
        "",
    ]
    for text, reply in turns:
        lines += [f"**Cliente:** {text}", ""]
        lines += [f"> tool: `{format_tool_call(t)}`" for t in reply.tool_calls]
        if reply.tool_calls:
            lines.append("")
        lines += [f"**Assistente:** {reply.text}", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with the Empório da Música assistant.")
    parser.add_argument("--provider", choices=["anthropic", "ollama"], help="overrides LLM_PROVIDER")
    parser.add_argument("--debug", action="store_true", help="show tool calls")
    args = parser.parse_args()

    for stream in (sys.stdin, sys.stdout):
        stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    settings = load_settings(args.provider)
    session = create_session(settings)
    debug = args.debug
    turns: list[tuple[str, Reply]] = []
    print(f"Empório da Música — assistente virtual ({session.llm.name}). Digite /sair para encerrar.\n")

    while True:
        try:
            text = input("Você: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/sair", "/quit", "/exit"):
            break
        if text == "/reset":
            session, turns = create_session(settings), []
            print("(nova conversa)\n")
            continue
        if text == "/debug":
            debug = not debug
            print(f"(debug {'ligado' if debug else 'desligado'})\n")
            continue
        if text.startswith("/save"):
            name = text.removeprefix("/save").strip() or f"conversa-{datetime.now():%Y%m%d-%H%M%S}.md"
            path = PROJECT_ROOT / "transcripts" / name
            save_transcript(path, settings, session, turns)
            print(f"(conversa salva em {path})\n")
            continue

        reply = session.send(text)
        turns.append((text, reply))
        if debug:
            for trace in reply.tool_calls:
                print(f"  [tool] {format_tool_call(trace)}")
        print(f"Empório: {reply.text}\n")


if __name__ == "__main__":
    main()
