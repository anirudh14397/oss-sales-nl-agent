"""
Streamlit chat UI for the sales NL agent.

Run with: streamlit run app/streamlit_app.py
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.metrics_client import CERTIFIED_METRICS
from agent.orchestrator import answer

USER_AVATAR = "🧑‍💼"
ASSISTANT_AVATAR = "📊"

TYPE_BADGE = {
    "answer": ("✅", "Certified answer", "answer"),
    "clarify": ("❓", "Clarifying question", "clarify"),
    "refuse": ("🚫", "Refused", "refuse"),
    "exploratory": ("⚠️", "Exploratory — not certified", "exploratory"),
}

SAMPLE_PROMPTS = [
    ("📈", "What was our net revenue last quarter?"),
    ("🔁", "Which product category had the highest returns rate in Q2?"),
    ("🎯", "How are we tracking against target in North America this year?"),
    ("🌐", "How are we tracking against target in APAC this year?"),
    ("🔍", "How many distinct customers do we have in the Enterprise segment?"),
    ("🚫", "What's our total headcount?"),
]

st.set_page_config(page_title="Sales Q&A", page_icon="📊", layout="centered")

st.markdown(
    """
    <style>
        :root {
            --brand: #4f7cff;
            --brand-dark: #3a5ce0;
        }
        .block-container { padding-top: 2.5rem; max-width: 780px; }

        .hero-title {
            font-size: 2.1rem;
            font-weight: 800;
            letter-spacing: -0.03em;
            margin-bottom: 0.15rem;
        }
        .hero-sub {
            font-size: 1rem;
            opacity: 0.72;
            margin-bottom: 1.1rem;
            line-height: 1.5;
        }

        .legend { display: flex; gap: 0.6rem; flex-wrap: wrap; margin-bottom: 1.6rem; }
        .legend-pill {
            font-size: 0.78rem; font-weight: 600; padding: 0.28rem 0.7rem;
            border-radius: 999px; opacity: 0.85; white-space: nowrap;
        }
        .pill-answer { background: rgba(34,197,94,0.15); color: #16a34a; }
        .pill-clarify { background: rgba(99,102,241,0.15); color: #6366f1; }
        .pill-refuse { background: rgba(239,68,68,0.15); color: #ef4444; }
        .pill-exploratory { background: rgba(245,158,11,0.15); color: #d97706; }

        .prompt-label {
            font-size: 0.82rem; font-weight: 700; text-transform: uppercase;
            letter-spacing: 0.06em; opacity: 0.55; margin: 0.4rem 0 0.6rem 0;
        }

        div[data-testid="stButton"] > button {
            border-radius: 12px; border: 1px solid rgba(127,127,127,0.25);
            padding: 0.55rem 0.9rem; font-size: 0.88rem; text-align: left;
            width: 100%; transition: all 0.15s ease; background: rgba(127,127,127,0.04);
        }
        div[data-testid="stButton"] > button:hover {
            border-color: var(--brand); color: var(--brand); background: rgba(79,124,255,0.08);
        }

        div[data-testid="stChatMessage"] {
            border-radius: 16px; padding: 0.9rem 1.1rem; margin-bottom: 0.6rem;
        }

        .response-badge {
            display: inline-flex; align-items: center; gap: 0.35rem;
            font-size: 0.78rem; font-weight: 700; padding: 0.25rem 0.65rem;
            border-radius: 999px; margin-bottom: 0.5rem;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="hero-title">📊 Sales Q&A</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="hero-sub">Ask questions about sales performance in plain English. '
    "Answers are grounded in certified metrics — if something's genuinely ambiguous "
    "or out of scope, you'll get a question or a refusal instead of a guess.</div>",
    unsafe_allow_html=True,
)
st.markdown(
    """
    <div class="legend">
        <span class="legend-pill pill-answer">✅ Certified answer</span>
        <span class="legend-pill pill-clarify">❓ Clarifying question</span>
        <span class="legend-pill pill-exploratory">⚠️ Exploratory (unverified)</span>
        <span class="legend-pill pill-refuse">🚫 Refused</span>
    </div>
    """,
    unsafe_allow_html=True,
)

if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_question" not in st.session_state:
    st.session_state.pending_question = None

if not st.session_state.messages and not st.session_state.pending_question:
    st.markdown('<div class="prompt-label">Try asking</div>', unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (icon, prompt) in enumerate(SAMPLE_PROMPTS):
        if cols[i % 2].button(f"{icon}  {prompt}", key=f"sample_{i}"):
            st.session_state.pending_question = prompt
            st.rerun()
    st.divider()

with st.sidebar:
    st.markdown("### About")
    st.write(
        "This agent only answers from **certified metrics** defined in a dbt "
        "semantic layer — never a freeform SQL guess. Questions with no "
        "certified metric fall back to a clearly-labeled, read-only "
        "exploratory query, or get refused if they're out of scope."
    )
    st.markdown("### Certified metrics")
    for name, meta in CERTIFIED_METRICS.items():
        st.markdown(f"**`{name}`**")
        st.caption(meta["description"])


def render_message(msg: dict):
    badge = TYPE_BADGE.get(msg.get("response_type"))
    if badge:
        icon, label, css_class = badge
        st.markdown(
            f'<div class="response-badge pill-{css_class}">{icon} {label}</div>',
            unsafe_allow_html=True,
        )

    if msg.get("response_type") == "exploratory":
        st.warning(msg["content"])
    else:
        st.write(msg["content"])

    if msg.get("chart") is not None:
        st.plotly_chart(msg["chart"], use_container_width=True)

    if msg.get("metric_used") or msg.get("sql_used"):
        with st.expander("How I answered this"):
            if msg.get("metric_used"):
                st.write(f"Metric: `{msg['metric_used']}`")
            if msg.get("sql_used"):
                st.caption("Generated SQL — review before trusting this number:")
                st.code(msg["sql_used"], language="sql")


for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar=USER_AVATAR if msg["role"] == "user" else ASSISTANT_AVATAR):
        render_message(msg)

chat_input_value = st.chat_input("Ask a question about sales performance...")
question = st.session_state.pending_question or chat_input_value
st.session_state.pending_question = None

if question:
    history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user", avatar=USER_AVATAR):
        st.write(question)

    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        with st.spinner("Thinking..."):
            response = answer(question, history=history)
        assistant_msg = {
            "role": "assistant",
            "content": response.text,
            "chart": response.chart,
            "metric_used": response.metric_used,
            "response_type": response.type,
            "sql_used": response.sql_used,
        }
        render_message(assistant_msg)

    st.session_state.messages.append(assistant_msg)
