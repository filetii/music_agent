"""Browser chat with the agent.

    streamlit run src/emporio/web.py

Same session, tools and guards as the terminal chat; each reply can expand to show the
tool calls behind it, like `emporio --debug`.
"""

import logging

import streamlit as st

from emporio.agent import Reply, create_session
from emporio.config import load_settings

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
st.set_page_config(page_title="Empório da Música", page_icon="🎸")
settings = load_settings()

if "session" not in st.session_state:  # one agent session per browser tab
    st.session_state.session = create_session(settings)
    st.session_state.turns = []

with st.sidebar:
    st.markdown(f"**Modelo:** `{st.session_state.session.llm.name}`")
    st.markdown(f"**Data de referência:** {settings.reference_date:%d/%m/%Y}")
    show_tools = st.toggle("Mostrar chamadas de ferramentas", value=True)
    if st.button("Nova conversa"):
        del st.session_state["session"]
        st.rerun()


def markdown(text: str) -> None:
    st.markdown(text.replace("$", r"\$"))  # Streamlit reads "R$ 10 ... R$ 20" as LaTeX otherwise


def render(reply: Reply) -> None:
    if show_tools and reply.tool_calls:
        with st.expander(f"Ferramentas usadas: {', '.join(t.name for t in reply.tool_calls)}"):
            for trace in reply.tool_calls:
                st.markdown(f"`{trace.name}` → **{trace.result['status']}**")
                st.json({"arguments": trace.arguments, "result": trace.result}, expanded=False)
    markdown(reply.text)


st.title("Empório da Música")
st.caption("Assistente virtual: instrumentos, preços, pedidos e políticas da loja")

for text, reply in st.session_state.turns:
    with st.chat_message("user"):
        markdown(text)
    with st.chat_message("assistant"):
        render(reply)

if text := st.chat_input("Digite sua mensagem"):
    with st.chat_message("user"):
        markdown(text)
    with st.chat_message("assistant"):
        with st.spinner("Consultando..."):
            reply = st.session_state.session.send(text)
        render(reply)
    st.session_state.turns.append((text, reply))
