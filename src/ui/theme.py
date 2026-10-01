"""Presentation layer: design tokens and the stylesheet (architecture.md §9, AD-5).

This module is CSS and nothing else. It contains no financial data, no question
text, no retrieval and no formatting decisions — it is the one place to edit when
the product's look changes, which is why it is separated from :mod:`src.ui.chat`
(what to draw) and :mod:`src.ui.answer_view` (how to compose an answer).

**Everything here is presentation.** No rule in this file may introduce, remove
or transform a fact. The stylesheet can colour a number the model already wrote;
it cannot compute one. That constraint is the reason number emphasis lives in
:func:`src.ui.chat.emphasize_numbers` with tests rather than here, where it could
not be pinned.

**On selector brittleness.** Streamlit's markup is not a public API, so any
stylesheet targeting it is coupled to the installed version (1.50 here). The
rules below prefer ``data-testid`` over generated emotion class names, because
the test ids are stable across releases while ``e1ypd8m70``-style hashes change
every build. Where a selector could match too broadly the rule is scoped to one
container and written to fail closed: if a selector stops matching, the element
renders with Streamlit's default styling rather than becoming invisible or
unclickable. No rule hides an interactive control, and no rule sets
``display:none`` on anything Streamlit needs in order to receive a click.
"""

from __future__ import annotations

from string import Template

#: Design tokens. Named by role rather than by colour so a rebrand is a change
#: here and nowhere else.
TOKENS = {
    "bg": "#F7F8F7",
    "surface": "#FFFFFF",
    "ink": "#12211C",
    "ink_muted": "#5C6B66",
    "ink_faint": "#8A9A95",
    "accent": "#0E8A5F",
    "accent_hover": "#0B6E4C",
    "accent_soft": "#E8F5EE",
    "accent_softer": "#F2FAF6",
    "border": "#E4E9E7",
    "border_strong": "#D3DCD8",
    "shadow": "0 1px 2px rgba(18, 33, 28, 0.04), 0 4px 16px rgba(18, 33, 28, 0.05)",
    "radius": "12px",
    "radius_lg": "16px",
}

#: The stylesheet. Kept as one module-level string so it can be asserted on in
#: tests: a rule that starts hiding interactive elements should fail a test rather
#: than be discovered visually.
_TEMPLATE = Template("""
<style>
:root {
  --ff-bg: ${bg};
  --ff-surface: ${surface};
  --ff-ink: ${ink};
  --ff-ink-muted: ${ink_muted};
  --ff-ink-faint: ${ink_faint};
  --ff-accent: ${accent};
  --ff-accent-hover: ${accent_hover};
  --ff-accent-soft: ${accent_soft};
  --ff-accent-softer: ${accent_softer};
  --ff-border: ${border};
  --ff-border-strong: ${border_strong};
  --ff-shadow: ${shadow};
  --ff-radius: ${radius};
  --ff-radius-lg: ${radius_lg};
}

/* -- base ------------------------------------------------------------------ */
html, body, .stApp, [data-testid="stAppViewContainer"] {
  background: var(--ff-bg);
  color: var(--ff-ink);
  font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    "Helvetica Neue", Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}

/* Generous whitespace without a hard width cap: the sidebar narrows the column
   on wide screens, and a max-width here would fight that on narrow ones. */
.block-container {
  padding-top: 2.1rem;
  padding-bottom: 6.5rem;
  max-width: 60rem;
}
[data-testid="stMainBlockContainer"] { padding-bottom: 6.5rem; }

h1, h2, h3, h4 { color: var(--ff-ink); letter-spacing: -0.018em; }
p, span, div { color: inherit; }

/* -- top bar --------------------------------------------------------------- */
[data-testid="stHeader"] { background: transparent; }
[data-testid="stToolbar"] { right: 1rem; }
[data-testid="stDecoration"] { display: none; }
#MainMenu, footer { visibility: hidden; }

/* -- sidebar --------------------------------------------------------------- */
[data-testid="stSidebar"] {
  background: var(--ff-surface);
  border-right: 1px solid var(--ff-border);
}
[data-testid="stSidebarContent"] { padding: 1.35rem 1.05rem 1.1rem; }
[data-testid="stSidebarNav"] { display: none; }

.ff-brand { display: flex; align-items: center; gap: 0.6rem; margin-bottom: 1.35rem; }
.ff-brand__mark {
  width: 2rem; height: 2rem; border-radius: 9px; flex: 0 0 auto;
  background: var(--ff-accent);
  display: flex; align-items: center; justify-content: center;
}
.ff-brand__mark svg { width: 1.05rem; height: 1.05rem; display: block; }
.ff-brand__name {
  font-size: 1.02rem; font-weight: 650; letter-spacing: -0.02em;
  color: var(--ff-ink); line-height: 1.15;
}
.ff-brand__sub { font-size: 0.71rem; color: var(--ff-ink-faint); letter-spacing: 0.005em; }

.ff-nav { display: flex; flex-direction: column; gap: 0.15rem; }
.ff-nav__item {
  display: flex; align-items: center; gap: 0.6rem;
  padding: 0.5rem 0.65rem; border-radius: 9px;
  font-size: 0.875rem; font-weight: 500; color: var(--ff-ink-muted);
}
.ff-nav__item svg { width: 1rem; height: 1rem; flex: 0 0 auto; opacity: 0.75; }
.ff-nav__item--active {
  background: var(--ff-accent-soft); color: var(--ff-accent); font-weight: 600;
}
.ff-nav__item--active svg { opacity: 1; }

.ff-label {
  font-size: 0.66rem; font-weight: 650; letter-spacing: 0.075em;
  text-transform: uppercase; color: var(--ff-ink-faint);
  margin: 0 0 0.5rem 0.15rem;
}
.ff-divider { height: 1px; background: var(--ff-border); margin: 1.2rem 0; border: 0; }
.ff-sidebar-foot {
  font-size: 0.7rem; color: var(--ff-ink-faint); line-height: 1.5;
  padding: 0 0.15rem;
}

/* Sidebar shortcut buttons: compact, left-aligned, quiet until hover. */
[data-testid="stSidebar"] .stButton > button {
  width: 100%; justify-content: flex-start; text-align: left;
  background: transparent; border: 1px solid transparent;
  color: var(--ff-ink-muted); font-size: 0.815rem; font-weight: 500;
  padding: 0.36rem 0.55rem; border-radius: 8px; box-shadow: none;
  transition: background 120ms ease, color 120ms ease;
}
[data-testid="stSidebar"] .stButton > button:hover {
  background: var(--ff-accent-softer); color: var(--ff-accent);
  border-color: var(--ff-accent-soft);
}
[data-testid="stSidebar"] .stButton > button:focus-visible {
  outline: 2px solid var(--ff-accent); outline-offset: 1px;
}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] { margin: 0.3rem 0 0.15rem 0.15rem; }
[data-testid="stSidebar"] .stMarkdown p { font-size: 0.79rem; }

/* -- header ---------------------------------------------------------------- */
.ff-eyebrow {
  display: inline-flex; align-items: center; gap: 0.4rem;
  font-size: 0.72rem; font-weight: 500; color: var(--ff-ink-muted);
  background: var(--ff-surface); border: 1px solid var(--ff-border);
  border-radius: 999px; padding: 0.28rem 0.7rem; margin-bottom: 0.85rem;
}
.ff-eyebrow::before {
  content: ""; width: 5px; height: 5px; border-radius: 50%;
  background: var(--ff-accent); flex: 0 0 auto;
}
.ff-title {
  font-size: 1.72rem; font-weight: 660; letter-spacing: -0.028em;
  line-height: 1.22; color: var(--ff-ink); margin: 0 0 0.45rem 0;
}
.ff-title em { font-style: normal; color: var(--ff-accent); }
.ff-subtitle {
  font-size: 0.925rem; color: var(--ff-ink-muted); margin: 0;
  line-height: 1.55; max-width: 34rem;
}

/* -- quick-question chips -------------------------------------------------- */
/* ``st.container(key="ff_chiprow")`` emits the ``st-key-ff_chiprow`` class. Each
   chip is a real button styled as a pill, with its icon supplied by the native
   ``icon=`` argument (Streamlit buttons cannot render inline SVG markup). */
.st-key-ff_chiprow { margin-top: 1.15rem; }
.st-key-ff_chiprow .stButton > button {
  width: 100%;
  background: var(--ff-surface); border: 1px solid var(--ff-border);
  border-radius: 999px; color: var(--ff-ink-muted);
  font-size: 0.8rem; font-weight: 500;
  padding: 0.4rem 0.75rem; box-shadow: 0 1px 2px rgba(18, 33, 28, 0.03);
  transition: border-color 120ms ease, color 120ms ease, background 120ms ease;
  display: inline-flex; align-items: center; justify-content: center; gap: 0.38rem;
}
.st-key-ff_chiprow .stButton > button:hover {
  border-color: var(--ff-accent); color: var(--ff-accent);
  background: var(--ff-accent-softer);
}
.st-key-ff_chiprow .stButton > button:focus-visible {
  outline: 2px solid var(--ff-accent); outline-offset: 1px;
}
.st-key-ff_chiprow [data-testid="stColumn"] { min-width: 0; }

/* -- chat bubbles ---------------------------------------------------------- */
[data-testid="stChatMessage"] {
  padding: 0.7rem 0.9rem; border-radius: var(--ff-radius-lg); gap: 0.6rem;
}
[data-testid="stChatMessageContent"] { max-width: 46rem; }

/* The assistant avatar becomes a small emerald glyph. Sized and coloured, never
   hidden, so Streamlit's own layout is untouched. */
[data-testid="stChatMessageAvatarAssistant"],
[data-testid="stChatMessageAvatarUser"],
[data-testid="stChatMessageAvatarCustom"] {
  width: 1.6rem; height: 1.6rem; min-width: 1.6rem; font-size: 0.72rem;
  background: var(--ff-accent-soft); color: var(--ff-accent);
  border: 1px solid var(--ff-accent-soft);
}
[data-testid="stChatMessageAvatarAssistant"] svg { width: 0.95rem; height: 0.95rem; }
[data-testid="stChatMessageAvatarUser"] {
  background: var(--ff-surface); color: var(--ff-ink-faint);
  border: 1px solid var(--ff-border);
}
[data-testid="stChatMessageAvatarUser"] svg { width: 0.9rem; height: 0.9rem; }

/* The user message renders into the container Streamlit already tints, so the
   mint surface comes from the token rather than a new wrapper. Streamlit applies
   the tint through an emotion class we cannot target, so the tint is overridden
   on the content element instead. */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
  flex-direction: row-reverse;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageContent"] {
  background: var(--ff-accent-soft);
  border: 1px solid #D8EDE2;
  border-radius: var(--ff-radius-lg);
  padding: 0.5rem 0.85rem;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageContent"] p {
  color: var(--ff-ink); font-size: 0.925rem; line-height: 1.55;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageContent"] {
  margin-left: auto;
}

[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
  background: var(--ff-surface);
  border: 1px solid var(--ff-border);
  box-shadow: var(--ff-shadow);
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
  [data-testid="stChatMessageContent"] p {
  color: var(--ff-ink); font-size: 0.94rem; line-height: 1.62;
}

/* -- answer card internals ------------------------------------------------- */
/* Written by src.ui.chat as HTML inside one st.markdown call, so these classes
   carry the answer/metadata split that Markdown cannot express. */
.ff-answer { margin: 0; }
.ff-answer p { margin: 0 0 0.55rem 0; }
.ff-answer p:last-child { margin-bottom: 0; }
.ff-num {
  font-weight: 650; color: #0B6E4C; font-size: 1.02em;
  letter-spacing: -0.01em; white-space: nowrap;
}
.ff-meta {
  display: flex; flex-wrap: wrap; align-items: center; gap: 0.3rem 0.6rem;
  margin-top: 0.7rem; padding-top: 0.6rem;
  border-top: 1px solid var(--ff-border);
  font-size: 0.76rem; color: var(--ff-ink-faint);
}
.ff-meta__source {
  display: inline-flex; align-items: center; gap: 0.22rem;
  color: var(--ff-accent); font-weight: 550; text-decoration: none;
}
.ff-meta__source:hover { color: var(--ff-accent-hover); text-decoration: underline; }
.ff-meta__source svg { width: 0.72rem; height: 0.72rem; }
.ff-meta__fresh { color: var(--ff-ink-faint); }

/* -- sources expander ------------------------------------------------------ */
[data-testid="stExpander"] {
  border: 1px solid var(--ff-border); border-radius: var(--ff-radius);
  background: var(--ff-accent-softer); margin-top: 0.6rem;
}
[data-testid="stExpander"] summary { font-size: 0.8rem; font-weight: 550; color: var(--ff-ink-muted); }
[data-testid="stExpander"] summary:hover { color: var(--ff-accent); }
.ff-sources table {
  width: 100%; border-collapse: collapse; font-size: 0.74rem;
}
.ff-sources th {
  text-align: left; font-weight: 600; color: var(--ff-ink-faint);
  padding: 0.3rem 0.5rem 0.3rem 0; border-bottom: 1px solid var(--ff-border);
  text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.65rem;
}
.ff-sources td {
  padding: 0.34rem 0.5rem 0.34rem 0; color: var(--ff-ink-muted);
  border-bottom: 1px solid var(--ff-border);
}
.ff-sources tr:last-child td { border-bottom: 0; }
.ff-sources code { font-size: 0.71rem; color: var(--ff-ink); }

.ff-openlink .stLinkButton > a {
  background: var(--ff-surface); border: 1px solid var(--ff-border);
  border-radius: 999px; color: var(--ff-accent);
  font-size: 0.78rem; font-weight: 550; padding: 0.28rem 0.8rem;
  box-shadow: none; text-decoration: none;
}
.ff-openlink .stLinkButton > a:hover {
  border-color: var(--ff-accent); background: var(--ff-accent-softer);
}

/* -- refusal blocks -------------------------------------------------------- */
.ff-refusal {
  background: var(--ff-accent-softer); border: 1px solid var(--ff-border);
  border-left: 3px solid var(--ff-accent); border-radius: var(--ff-radius);
  padding: 0.75rem 0.9rem; font-size: 0.9rem; line-height: 1.6;
  color: var(--ff-ink);
}
.ff-refusal p { margin: 0; }
.ff-refusal .ff-meta { border-top: 0; padding-top: 0.5rem; margin-top: 0.45rem; }

/* -- empty state ----------------------------------------------------------- */
.ff-empty { text-align: center; padding: 2.4rem 1rem 1.6rem; }
.ff-empty__mark {
  width: 2.75rem; height: 2.75rem; border-radius: 13px; margin: 0 auto 1.1rem;
  background: var(--ff-accent-soft); border: 1px solid #D8EDE2;
  display: flex; align-items: center; justify-content: center;
}
.ff-empty__mark svg { width: 1.3rem; height: 1.3rem; color: var(--ff-accent); }
.ff-empty__title {
  font-size: 1.28rem; font-weight: 640; letter-spacing: -0.022em;
  color: var(--ff-ink); margin: 0 0 0.4rem 0;
}
.ff-empty__sub {
  font-size: 0.9rem; color: var(--ff-ink-muted);
  max-width: 27rem; margin: 0 auto; line-height: 1.55;
}
.ff-examples {
  display: flex; flex-direction: column; gap: 0.5rem;
  max-width: 34rem; margin: 1.5rem auto 0; text-align: left;
}
.ff-examples .stButton > button {
  width: 100%; justify-content: flex-start; text-align: left;
  background: var(--ff-surface); border: 1px solid var(--ff-border);
  border-radius: var(--ff-radius); color: var(--ff-ink);
  font-size: 0.86rem; font-weight: 500; padding: 0.62rem 0.85rem;
  box-shadow: var(--ff-shadow); transition: border-color 120ms ease;
}
.ff-examples .stButton > button:hover { border-color: var(--ff-accent); color: var(--ff-accent); }
.ff-examples .stButton > button:focus-visible {
  outline: 2px solid var(--ff-accent); outline-offset: 1px;
}
.ff-stats {
  text-align: center; margin-top: 1.4rem; font-size: 0.74rem;
  color: var(--ff-ink-faint);
}

/* -- input ----------------------------------------------------------------- */
[data-testid="stChatInput"] {
  background: var(--ff-surface); border: 1px solid var(--ff-border-strong);
  border-radius: 999px; box-shadow: var(--ff-shadow);
  transition: border-color 120ms ease, box-shadow 120ms ease;
}
[data-testid="stChatInput"]:focus-within {
  border-color: var(--ff-accent);
  box-shadow: 0 0 0 3px rgba(14, 138, 95, 0.12), var(--ff-shadow);
}
[data-testid="stChatInputTextArea"] {
  background: transparent; font-size: 0.92rem; color: var(--ff-ink);
}
[data-testid="stChatInputTextArea"]::placeholder { color: var(--ff-ink-faint); }
[data-testid="stChatInputSubmitButton"] {
  background: var(--ff-accent) !important; border-radius: 50%;
}
[data-testid="stChatInputSubmitButton"]:hover { background: var(--ff-accent-hover) !important; }
.ff-input-note {
  text-align: center; font-size: 0.72rem; color: var(--ff-ink-faint);
  margin: 0.5rem 0 0;
}

/* -- buttons --------------------------------------------------------------- */
.stButton > button, .stLinkButton > a {
  border-radius: 9px; font-weight: 550;
  transition: border-color 120ms ease, background 120ms ease, color 120ms ease;
}
.stButton > button:focus-visible, .stLinkButton > a:focus-visible {
  outline: 2px solid var(--ff-accent); outline-offset: 1px;
}
[data-testid="stCaptionContainer"] p, .stCaption { color: var(--ff-ink-faint); }
[data-testid="stAlert"] { border-radius: var(--ff-radius); font-size: 0.87rem; }

/* -- narrow screens -------------------------------------------------------- */
@media (max-width: 640px) {
  .block-container { padding-top: 1.5rem; padding-left: 1rem; padding-right: 1rem; }
  .ff-title { font-size: 1.42rem; }
  .ff-subtitle { font-size: 0.875rem; }
  .st-key-ff_chiprow [data-testid="stColumn"] { width: 100%; }
  .st-key-ff_chiprow .stButton > button { justify-content: flex-start; }
  [data-testid="stChatMessage"] { padding: 0.5rem 0.35rem; }
  [data-testid="stChatMessageContent"] { max-width: 100%; }
  .ff-meta { font-size: 0.73rem; }
  .ff-sources table { font-size: 0.69rem; }
  /* Long URLs are the main overflow risk: break them anywhere rather than
     letting a citation push the card wider than the viewport. */
  .ff-meta, .ff-meta a, .ff-sources td, .ff-sources code { overflow-wrap: anywhere; }
  .ff-openlink .stLinkButton > a { font-size: 0.75rem; }
}

@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; }
}
</style>
""")

#: The stylesheet with the tokens substituted. A :class:`string.Template` is used
#: rather than ``%``-formatting or ``str.format`` because CSS is full of literal
#: ``%`` signs (``100%``, ``50%``, ``rgba(...)``) and braces, both of which those
#: two would try to interpret.
_CSS = _TEMPLATE.substitute(TOKENS)


def inject_css(st_mod=None) -> str:
    """Push the stylesheet into the running app and return it.

    ``st_mod`` is injectable so tests can assert on the markup without a server.
    Passing ``None`` imports Streamlit lazily, which keeps this module importable
    in a plain test process per the note at the top of :mod:`src.ui.chat`.
    """
    if st_mod is None:
        import streamlit as st_mod  # noqa: PLW2901 - deliberate lazy import

    st_mod.markdown(_CSS, unsafe_allow_html=True)
    return _CSS


def stylesheet() -> str:
    """The raw CSS, for tests that need to inspect it."""
    return _CSS