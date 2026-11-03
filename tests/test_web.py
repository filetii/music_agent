"""The Streamlit page, driven headless with a scripted fake LLM (no browser, no network)."""

from dataclasses import replace
from datetime import datetime

import pytest

from emporio import agent
from emporio.config import PROJECT_ROOT, load_settings
from test_agent import ScriptedLLM, answer, tool_call

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


def test_chat_turn_shows_reply_with_its_tool_calls(monkeypatch, tmp_path):
    llm = ScriptedLLM([tool_call("get_product_details", name="Yamaha C40"), answer("O Yamaha C40 custa R$ 599,90.")])
    real_create_session = agent.create_session
    settings = replace(load_settings(), handoffs_path=tmp_path / "handoffs.db")
    monkeypatch.setattr(
        agent, "create_session",
        lambda _settings: real_create_session(settings, llm=llm, now=datetime(2026, 3, 25, 10, 0)),
    )

    app = AppTest.from_file(str(PROJECT_ROOT / "src" / "emporio" / "web.py")).run()
    app.chat_input[0].set_value("Quanto custa o Yamaha C40?").run()

    assert not app.exception
    user, assistant = app.chat_message
    assert user.markdown[0].value == "Quanto custa o Yamaha C40?"
    assert assistant.markdown[-1].value == r"O Yamaha C40 custa R\$ 599,90."  # "$" escaped, not LaTeX
    assert app.expander[0].label == "Ferramentas usadas: get_product_details"
    assert "ok" in assistant.markdown[0].value
