"""Inline SVG icons for the UI (presentation only).

Streamlit 1.50 can take ``:material/name:`` avatars, and those are used wherever a
chat message needs one. These constants exist for the places Streamlit does not
give a hook: the sidebar, the header chips and the empty-state mark, which are all
drawn as raw HTML inside a single ``st.markdown`` call.

Each value is the *inside* of a 24×24 viewBox, stroked with ``currentColor`` so the
surrounding CSS decides the colour. They are geometric shapes only — no data, no
text, and no claim about a source or a certification.
"""

from __future__ import annotations

#: Rounded square with a rising bar chart: the brand mark and the empty state.
ICON_LOGO = (
    '<rect x="3.5" y="3.5" width="17" height="17" rx="4.5" stroke="currentColor" '
    'stroke-width="1.6" fill="none"/>'
    '<path d="M8 15.2V12.4M11.4 15.2V9.6M14.8 15.2V11.2" stroke="currentColor" '
    'stroke-width="1.7" stroke-linecap="round" fill="none"/>'
)

ICON_CHAT = (
    '<path d="M20.5 11.6c0 4.1-3.8 7.4-8.5 7.4-1 0-2-.15-2.9-.43L4 20.5l1.3-3.4'
    'c-1.1-1.1-1.8-2.5-1.8-4 0-4.1 3.8-7.4 8.5-7.4s8.5 3.3 8.5 7.4Z" '
    'stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" fill="none"/>'
)

ICON_INFO = (
    '<circle cx="12" cy="12" r="8.6" stroke="currentColor" stroke-width="1.6" fill="none"/>'
    '<path d="M12 11.1v5M12 7.9h.01" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round" fill="none"/>'
)

ICON_SOURCES = (
    '<path d="M5.5 5.2A1.7 1.7 0 0 1 7.2 3.5h10.1c.5 0 1 .2 1.3.6l1.4 1.5v11.8'
    'a1.7 1.7 0 0 1-1.7 1.7H7.2a1.7 1.7 0 0 1-1.7-1.7V5.2Z" stroke="currentColor" '
    'stroke-width="1.6" stroke-linejoin="round" fill="none"/>'
    '<path d="M9 11.4h6M9 14.6h4" stroke="currentColor" stroke-width="1.6" '
    'stroke-linecap="round" fill="none"/>'
)

ICON_SPARK = (
    '<path d="M12 3.6l1.9 4.9 4.9 1.9-4.9 1.9L12 17.2l-1.9-4.9-4.9-1.9 4.9-1.9L12 3.6Z" '
    'stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" fill="none"/>'
    '<path d="M18.4 15.6l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8.8-2Z" '
    'stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" fill="none"/>'
)

ICON_PERCENT = (
    '<path d="M18.6 5.4L5.4 18.6" stroke="currentColor" stroke-width="1.7" '
    'stroke-linecap="round" fill="none"/>'
    '<circle cx="7.8" cy="7.8" r="2.3" stroke="currentColor" stroke-width="1.6" fill="none"/>'
    '<circle cx="16.2" cy="16.2" r="2.3" stroke="currentColor" stroke-width="1.6" fill="none"/>'
)

ICON_SCALE = (
    '<path d="M4 20h16" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" fill="none"/>'
    '<path d="M12 20V8.6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" fill="none"/>'
    '<path d="M12 8.6h6.4M18.4 8.6l-2.6 5.2h5.2L18.4 8.6Z" stroke="currentColor" '
    'stroke-width="1.5" stroke-linejoin="round" fill="none"/>'
)

ICON_MANAGER = (
    '<circle cx="12" cy="8.4" r="3.6" stroke="currentColor" stroke-width="1.6" fill="none"/>'
    '<path d="M5.4 19.6a6.6 6.6 0 0 1 13.2 0" stroke="currentColor" stroke-width="1.6" '
    'stroke-linecap="round" fill="none"/>'
)

ICON_EXIT = (
    '<path d="M13.6 4.6H7.4a1.7 1.7 0 0 0-1.7 1.7v11.4a1.7 1.7 0 0 0 1.7 1.7h6.2" '
    'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" fill="none"/>'
    '<path d="M16.2 8.2L19.8 12l-3.6 3.8M19.2 12h-8.6" stroke="currentColor" '
    'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
)

ICON_MIN_SIP = (
    '<rect x="4.2" y="6.6" width="15.6" height="10.8" rx="2" stroke="currentColor" '
    'stroke-width="1.6" fill="none"/>'
    '<path d="M8.4 6.6V5.4a1.6 1.6 0 0 1 1.6-1.6h4a1.6 1.6 0 0 1 1.6 1.6v1.2" '
    'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" fill="none"/>'
    '<path d="M9.6 13.6h4.8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" fill="none"/>'
)

ICON_RISK = (
    '<path d="M12 4.4l7.4 13.1a1.2 1.2 0 0 1-1 1.8H5.6a1.2 1.2 0 0 1-1-1.8L12 4.4Z" '
    'stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" fill="none"/>'
    '<path d="M12 10v3.4M12 16.2h.01" stroke="currentColor" stroke-width="1.7" '
    'stroke-linecap="round" fill="none"/>'
)

ICON_BENCHMARK = (
    '<path d="M4.4 18.2V9.6M9.6 18.2V5.4M14.8 18.2v-6M20 18.2v-9.6" '
    'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" fill="none"/>'
)

#: External-link glyph, used beside the source label.
ICON_EXTERNAL = (
    '<path d="M13.6 4.8h5.6v5.6M19.2 4.8l-7.6 7.6" stroke="currentColor" '
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
    '<path d="M17.4 14.2v3.9a1.5 1.5 0 0 1-1.5 1.5H6.3a1.5 1.5 0 0 1-1.5-1.5V8.1'
    'a1.5 1.5 0 0 1 1.5-1.5h3.9" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
)

ICON_CHEVRON = (
    '<path d="M9.4 5.6l6.4 6.4-6.4 6.4" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
)
