"""EvalForge's accessible visual tokens and static Streamlit theme layer."""

from __future__ import annotations

import streamlit as st

TOKENS = {
    "indigo": "#6558F5",
    "indigo_dark": "#4F46D9",
    "cyan": "#16B8C8",
    "amber": "#E8A317",
    "coral": "#EF6A67",
    "green": "#1E9E72",
    "slate_950": "#111827",
    "slate_800": "#263247",
    "slate_600": "#526078",
    "slate_300": "#CBD3E0",
    "slate_100": "#F1F4F9",
    "surface": "#FFFFFF",
    "surface_alt": "#F7F8FC",
}

_STATIC_CSS = """
<style>
:root {
  --ef-indigo: #6558F5;
  --ef-indigo-dark: #4F46D9;
  --ef-cyan: #16B8C8;
  --ef-amber: #E8A317;
  --ef-coral: #EF6A67;
  --ef-green: #1E9E72;
  --ef-text: #111827;
  --ef-muted: #526078;
  --ef-border: #D9DFEA;
  --ef-surface: #FFFFFF;
  --ef-surface-alt: #F7F8FC;
  --ef-radius: 0.85rem;
  --ef-shadow: 0 10px 30px rgba(41, 50, 76, 0.08);
}

[data-testid="stAppViewContainer"] {
  background:
    radial-gradient(circle at 92% 2%, rgba(22, 184, 200, 0.08), transparent 24rem),
    radial-gradient(circle at 8% 0%, rgba(101, 88, 245, 0.09), transparent 28rem),
    var(--ef-surface-alt);
}

[data-testid="stMainBlockContainer"] {
  max-width: 92rem;
  padding-top: 2rem;
  padding-bottom: 4rem;
}

[data-testid="stSidebar"] {
  border-right: 1px solid var(--ef-border);
  background: rgba(255, 255, 255, 0.96);
}

[data-testid="stSidebarNav"] a[aria-current="page"] {
  background: rgba(101, 88, 245, 0.11);
  color: var(--ef-indigo-dark);
  font-weight: 700;
}

h1, h2, h3 {
  color: var(--ef-text);
  letter-spacing: -0.025em;
}

p, label, [data-testid="stCaptionContainer"] {
  color: var(--ef-muted);
}

[data-testid="stMetric"],
[data-testid="stVerticalBlockBorderWrapper"] {
  border-color: var(--ef-border);
  border-radius: var(--ef-radius);
  background: rgba(255, 255, 255, 0.93);
  box-shadow: 0 1px 2px rgba(41, 50, 76, 0.04);
}

[data-testid="stMetric"] {
  padding: 1rem 1.1rem;
}

[data-testid="stMetricLabel"] {
  font-weight: 650;
}

.stButton > button[kind="primary"],
.stDownloadButton > button[kind="primary"] {
  border: 0;
  background: linear-gradient(135deg, var(--ef-indigo), var(--ef-indigo-dark));
  box-shadow: 0 6px 18px rgba(79, 70, 217, 0.2);
}

.stButton > button,
.stDownloadButton > button {
  min-height: 2.75rem;
  border-radius: 0.7rem;
  font-weight: 700;
}

button:focus-visible,
a:focus-visible,
input:focus-visible,
textarea:focus-visible,
[role="button"]:focus-visible,
[role="option"]:focus-visible {
  outline: 3px solid rgba(22, 184, 200, 0.55) !important;
  outline-offset: 2px !important;
}

[data-testid="stDataFrame"],
[data-testid="stPlotlyChart"] {
  border-radius: var(--ef-radius);
  overflow: hidden;
}

@media (max-width: 48rem) {
  [data-testid="stMainBlockContainer"] {
    padding-left: 1rem;
    padding-right: 1rem;
    padding-top: 1rem;
  }

  [data-testid="stHorizontalBlock"] {
    gap: 0.65rem;
  }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
  }
}
</style>
"""


def apply_theme() -> None:
    """Apply only static trusted CSS; user/API text never enters HTML."""

    st.markdown(_STATIC_CSS, unsafe_allow_html=True)
