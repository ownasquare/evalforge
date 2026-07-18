"""EvalForge's accessible visual tokens and static Streamlit theme layer."""

from __future__ import annotations

import streamlit as st

TOKENS = {
    "accent": "#255F7A",
    "accent_hover": "#194A62",
    "focus": "#4F8EAA",
    "amber": "#A96C28",
    "red": "#AD4F4C",
    "green": "#35775D",
    "ink": "#18201C",
    "text": "#34413A",
    "muted": "#667169",
    "border": "#D9DEDA",
    "canvas": "#F5F6F4",
    "surface": "#FFFFFF",
    "surface_alt": "#FAFBFA",
}

_STATIC_CSS = """
<style>
:root {
  --primary-color: #255F7A;
  --ef-accent: #255F7A;
  --ef-accent-hover: #194A62;
  --ef-focus: #4F8EAA;
  --ef-success: #35775D;
  --ef-warning: #A96C28;
  --ef-danger: #AD4F4C;
  --ef-ink: #18201C;
  --ef-text: #34413A;
  --ef-muted: #667169;
  --ef-border: #D9DEDA;
  --ef-canvas: #F5F6F4;
  --ef-surface: #FFFFFF;
  --ef-surface-alt: #FAFBFA;
  --ef-radius: 0.625rem;
  --ef-shadow: 0 1px 2px rgba(24, 32, 28, 0.05);
}

[data-testid="stAppViewContainer"] {
  background: var(--ef-canvas);
}

[data-testid="stHeader"] {
  background: transparent;
}

[data-testid="stMainBlockContainer"] {
  max-width: 88rem;
  padding-top: 1.5rem;
  padding-bottom: 4rem;
}

[data-testid="stSidebar"] {
  border-right: 1px solid var(--ef-border);
  background: var(--ef-surface);
}

[data-testid="stSidebarNav"] [data-testid="stSidebarNavItems"] {
  gap: 0.15rem;
}

[data-testid="stSidebarNav"] a {
  border-radius: 0.5rem;
  color: var(--ef-text);
}

[data-testid="stSidebarNav"] a[aria-current="page"] {
  background: #EAF0F2;
  box-shadow: inset 3px 0 0 var(--ef-accent);
  color: var(--ef-accent-hover);
  font-weight: 650;
}

h1, h2, h3 {
  color: var(--ef-ink);
  letter-spacing: -0.02em;
}

h1 {
  font-size: 2rem;
  line-height: 1.2;
}

h2, h3 {
  line-height: 1.3;
}

p, label, [data-testid="stCaptionContainer"] {
  color: var(--ef-muted);
}

[data-testid="stMetric"] {
  border: 1px solid var(--ef-border);
  border-radius: var(--ef-radius);
  background: var(--ef-surface);
  box-shadow: var(--ef-shadow);
}

[data-testid="stMetric"] {
  padding: 0.9rem 1rem;
}

[data-testid="stMetricLabel"] {
  font-weight: 600;
}

[data-testid="stVerticalBlockBorderWrapper"] {
  border-color: var(--ef-border);
  border-radius: var(--ef-radius);
  background: var(--ef-surface);
  box-shadow: var(--ef-shadow);
}

.stButton > button[kind="primary"],
.stDownloadButton > button[kind="primary"] {
  border: 1px solid var(--ef-accent);
  background: var(--ef-accent);
  box-shadow: none;
  color: #FFFFFF;
}

.stButton > button[kind="primary"] *,
.stDownloadButton > button[kind="primary"] * {
  color: #FFFFFF !important;
}

.stButton > button[kind="primary"]:hover,
.stDownloadButton > button[kind="primary"]:hover {
  border-color: var(--ef-accent-hover);
  background: var(--ef-accent-hover);
  color: #FFFFFF;
}

.stButton > button,
.stDownloadButton > button {
  min-height: 2.5rem;
  border-radius: 0.5rem;
  font-weight: 650;
}

.stButton > button[kind="secondary"],
.stDownloadButton > button[kind="secondary"] {
  border-color: var(--ef-border);
  background: var(--ef-surface);
  color: var(--ef-text);
}

.stButton > button[kind="secondary"]:hover,
.stDownloadButton > button[kind="secondary"]:hover {
  border-color: #AEB8B1;
  background: var(--ef-surface-alt);
  color: var(--ef-ink);
}

button:focus-visible,
a:focus-visible,
input:focus-visible,
textarea:focus-visible,
[role="button"]:focus-visible,
[role="option"]:focus-visible {
  outline: 3px solid rgba(79, 142, 170, 0.48) !important;
  outline-offset: 2px !important;
}

[data-testid="stAlert"],
[data-testid="stExpander"] {
  border-radius: var(--ef-radius);
}

[data-testid="stDataFrame"],
[data-testid="stPlotlyChart"] {
  border-radius: var(--ef-radius);
  overflow: hidden;
}

input,
textarea,
[data-baseweb="select"] > div {
  border-radius: 0.5rem !important;
}

input[type="checkbox"],
input[type="radio"] {
  accent-color: var(--ef-accent);
}

hr {
  border-color: var(--ef-border);
}

@media (max-width: 48rem) {
  [data-testid="stMainBlockContainer"] {
    padding-left: 1rem;
    padding-right: 1rem;
    padding-top: 1rem;
  }

  h1 {
    font-size: 1.75rem;
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
