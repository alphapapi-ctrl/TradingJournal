"""
Theme manager for Trading Journal.
Three modes: dark / mid / light
"""
import json
from pathlib import Path

THEME_FILE  = Path(__file__).parent.parent / "data" / "theme.json"
VALID_THEMES = ("dark", "mid", "light")


def get_theme() -> str:
    try:
        if THEME_FILE.exists():
            t = json.loads(THEME_FILE.read_text()).get("theme", "dark")
            t = t if t in VALID_THEMES else "dark"
            _write_streamlit_theme(t)
            return t
    except Exception:
        pass
    return "dark"


def set_theme(theme: str):
    if theme not in VALID_THEMES:
        theme = "dark"
    THEME_FILE.parent.mkdir(parents=True, exist_ok=True)
    THEME_FILE.write_text(json.dumps({"theme": theme}, indent=2))
    _write_streamlit_theme(theme)


def _write_streamlit_theme(theme: str):
    """Sync Streamlit's own config.toml [theme] block so native components match."""
    import re
    config_path = Path(__file__).parent.parent / ".streamlit" / "config.toml"
    if not config_path.exists():
        return
    p = _PALETTES.get(theme, _PALETTES["dark"])
    base = "light" if theme == "light" else "dark"
    theme_block = (
        f'\n[theme]\n'
        f'base = "{base}"\n'
        f'primaryColor = "{p["--accent"]}"\n'
        f'backgroundColor = "{p["--bg-app"]}"\n'
        f'secondaryBackgroundColor = "{p["--bg-card"]}"\n'
        f'textColor = "{p["--text-primary"]}"\n'
    )
    content = config_path.read_text(encoding="utf-8")
    content = re.sub(r'\[theme\].*?(?=\n\[|\Z)', '', content, flags=re.DOTALL).strip()
    config_path.write_text(content + theme_block, encoding="utf-8")


# ── Palettes ──────────────────────────────────────────────────────────────────

_PALETTES = {
    "dark": {
        "--bg-app":        "#0a0c10",
        "--bg-sidebar":    "#0d0f14",
        "--bg-sidebar2":   "#131720",
        "--bg-card":       "#131720",
        "--bg-card2":      "#1a2030",
        "--bg-input":      "#131720",
        "--bg-active":     "#1e2a3a",
        "--bg-hover":      "#1e2533",
        "--border":        "#1e2533",
        "--border2":       "#2a3a55",
        "--text-primary":  "#e8eaf0",
        "--text-secondary":"#c8d0e0",
        "--text-muted":    "#6b7a99",
        "--text-faint":    "#3d4a66",
        "--text-sidebar":  "#c8d0e0",
        "--accent":        "#00c896",
        "--accent-dark":   "#00a07a",
        "--accent-bright": "#00e0aa",
        "--danger":        "#ff4b6e",
        "--warning":       "#f5a623",
        "--chart-grid":    "#1a2030",
        "--chart-text":    "#9aa4bc",
        "--table-bg":      "transparent",
        "--table-border":  "#1e2533",
    },
    "mid": {
        "--bg-app":        "#1a1f2e",
        "--bg-sidebar":    "#141826",
        "--bg-sidebar2":   "#1a1f2e",
        "--bg-card":       "#212840",
        "--bg-card2":      "#2a3350",
        "--bg-input":      "#212840",
        "--bg-active":     "#2a3a5c",
        "--bg-hover":      "#2a3350",
        "--border":        "#2e3c58",
        "--border2":       "#3a4d70",
        "--text-primary":  "#dde2f0",
        "--text-secondary":"#b8c2d8",
        "--text-muted":    "#7a8aac",
        "--text-faint":    "#4a5a7a",
        "--text-sidebar":  "#b8c2d8",
        "--accent":        "#00c896",
        "--accent-dark":   "#00a07a",
        "--accent-bright": "#00e0aa",
        "--danger":        "#ff4b6e",
        "--warning":       "#f5a623",
        "--chart-grid":    "#2a3350",
        "--chart-text":    "#8a9ab8",
        "--table-bg":      "transparent",
        "--table-border":  "#2e3c58",
    },
    "light": {
        "--bg-app":        "#f4f6fa",
        "--bg-sidebar":    "#eef0f6",
        "--bg-sidebar2":   "#e8eaf2",
        "--bg-card":       "#ffffff",
        "--bg-card2":      "#f0f2f8",
        "--bg-input":      "#ffffff",
        "--bg-active":     "#e0f5ef",
        "--bg-hover":      "#eef0f6",
        "--border":        "#d0d6e8",
        "--border2":       "#b8c0d8",
        "--text-primary":  "#1a2035",
        "--text-secondary":"#2d3a5a",
        "--text-muted":    "#5a6888",
        "--text-faint":    "#9aa4bc",
        "--text-sidebar":  "#2d3a5a",
        "--accent":        "#00a87a",
        "--accent-dark":   "#007a58",
        "--accent-bright": "#00c896",
        "--danger":        "#d93050",
        "--warning":       "#d4820a",
        "--chart-grid":    "#e0e4f0",
        "--chart-text":    "#3a4a6a",   # dark enough to read on white bg
        "--table-bg":      "#ffffff",
        "--table-border":  "#d0d6e8",
    },
}


def get_chart_font_color(theme: str) -> str:
    """Return the correct text colour for Plotly charts in the given theme."""
    return _PALETTES.get(theme, _PALETTES["dark"])["--chart-text"]


def get_chart_grid_color(theme: str) -> str:
    return _PALETTES.get(theme, _PALETTES["dark"])["--chart-grid"]


def get_full_css(theme: str) -> str:
    p = _PALETTES.get(theme, _PALETTES["dark"])
    vars_css = "\n  ".join(f"{k}: {v};" for k, v in p.items())

    return f"""
:root {{ {vars_css} }}

/* ── Fonts ── */
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Syne:wght@400;600;700;800&display=swap');
html, body, [class*="css"] {{ font-family: 'Syne', sans-serif; }}
code, pre {{ font-family: 'JetBrains Mono', monospace !important; }}

/* ── Kill the black Streamlit header/toolbar ── */
[data-testid="stHeader"] {{
    background: {p['--bg-app']} !important;
    border-bottom: 1px solid {p['--border']} !important;
}}
[data-testid="stToolbar"] {{
    background: {p['--bg-app']} !important;
}}
/* The deploy/hamburger buttons in the header */
[data-testid="stHeader"] button, [data-testid="stHeader"] svg {{
    color: {p['--text-muted']} !important;
    fill: {p['--text-muted']} !important;
}}
/* Top decoration bar Streamlit adds */
[data-testid="stDecoration"] {{
    background: {p['--bg-app']} !important;
    display: none;
}}

/* ── App background ── */
.stApp {{ background: {p['--bg-app']} !important; }}
.main .block-container {{ padding-top: 1rem; background: {p['--bg-app']}; }}
/* Main content area */
section[data-testid="stMain"] {{
    background: {p['--bg-app']} !important;
}}

/* ── Sidebar ── */
[data-testid="stSidebar"] {{
    background: linear-gradient(180deg, {p['--bg-sidebar']} 0%, {p['--bg-sidebar2']} 100%) !important;
    border-right: 1px solid {p['--border']} !important;
}}
[data-testid="stSidebar"] * {{ color: {p['--text-sidebar']} !important; }}
[data-testid="stSidebar"] .stButton button {{
    background: transparent !important; border: 1px solid {p['--border']} !important;
    color: {p['--text-sidebar']} !important; text-align: left; border-radius: 6px;
    transition: all 0.15s; margin-bottom: 2px;
}}
[data-testid="stSidebar"] .stButton button:hover {{
    background: {p['--bg-hover']} !important; border-color: {p['--accent']} !important;
    color: {p['--accent']} !important;
}}

/* ── Typography ── */
h1, h2, h3, h4, h5 {{
    font-family: 'Syne', sans-serif !important; font-weight: 700 !important;
    color: {p['--text-primary']} !important;
}}
p, li, label, .stMarkdown p, .stMarkdown li {{
    color: {p['--text-primary']} !important;
}}
.stCaption, .stCaption p {{ color: {p['--text-muted']} !important; }}

/* ── Metrics ── */
[data-testid="stMetric"] {{
    background: {p['--bg-card']} !important; border: 1px solid {p['--border']} !important;
    border-radius: 10px !important; padding: 12px 16px !important;
}}
[data-testid="stMetricLabel"] {{
    color: {p['--text-muted']} !important; font-size: 0.73rem !important;
    text-transform: uppercase; letter-spacing: 0.5px;
}}
[data-testid="stMetricValue"] {{
    color: {p['--text-primary']} !important; font-family: 'JetBrains Mono', monospace !important;
}}
[data-testid="stMetricDelta"] {{ font-family: 'JetBrains Mono', monospace !important; }}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {{
    background: {p['--bg-card']} !important; border-radius: 8px;
    padding: 4px; gap: 4px; border: 1px solid {p['--border']} !important;
}}
.stTabs [data-baseweb="tab"] {{ border-radius: 6px !important; color: {p['--text-muted']} !important; font-weight: 600; }}
.stTabs [aria-selected="true"] {{ background: {p['--bg-active']} !important; color: {p['--accent']} !important; }}

/* ── Buttons ── */
.stButton button[kind="primary"] {{
    background: linear-gradient(135deg, {p['--accent']}, {p['--accent-dark']}) !important;
    color: #fff !important; font-weight: 700; border: none !important; border-radius: 8px;
}}
.stButton button[kind="primary"]:hover {{
    background: linear-gradient(135deg, {p['--accent-bright']}, {p['--accent']}) !important;
}}
.stButton button[kind="secondary"] {{
    background: transparent !important; border: 1px solid {p['--border']} !important;
    color: {p['--text-secondary']} !important; border-radius: 8px;
}}
.stButton button[kind="secondary"]:hover {{ border-color: {p['--danger']} !important; color: {p['--danger']} !important; }}

/* ── Inputs ── */
input, textarea {{
    background: {p['--bg-input']} !important; border: 1px solid {p['--border']} !important;
    color: {p['--text-primary']} !important; border-radius: 6px !important;
}}
input:focus, textarea:focus {{ border-color: {p['--accent']} !important; box-shadow: 0 0 0 1px {p['--accent']} !important; }}

/* ── Select / Dropdown — target all nested BaseUI layers ── */
[data-baseweb="select"] > div,
[data-baseweb="select"] > div > div,
[data-baseweb="select"] div[class*="valueContainer"],
[data-baseweb="select"] div[class*="singleValue"],
[data-baseweb="select"] div[class*="placeholder"] {{
    background: {p['--bg-input']} !important;
    color: {p['--text-primary']} !important;
}}
[data-baseweb="select"] > div {{
    border-color: {p['--border']} !important;
}}
/* Selected value text and placeholder */
[data-baseweb="select"] span,
[data-baseweb="select"] div[data-id="selected"] {{
    color: {p['--text-primary']} !important;
}}
/* Dropdown indicator SVGs */
[data-baseweb="select"] svg {{ fill: {p['--text-muted']} !important; color: {p['--text-muted']} !important; }}

/* Dropdown list (portal rendered at body level) */
[data-baseweb="popover"] {{ background: {p['--bg-card']} !important; border: 1px solid {p['--border']} !important; border-radius: 8px !important; }}
[data-baseweb="menu"] {{ background: {p['--bg-card']} !important; }}
[data-baseweb="menu"] ul {{ background: {p['--bg-card']} !important; }}
[data-baseweb="option"] {{
    background: {p['--bg-card']} !important;
    color: {p['--text-primary']} !important;
}}
[data-baseweb="option"]:hover,
[data-baseweb="option"][aria-selected="true"] {{
    background: {p['--bg-hover']} !important;
    color: {p['--accent']} !important;
}}
/* Generic listbox / combobox in portals */
ul[role="listbox"], div[role="listbox"] {{
    background: {p['--bg-card']} !important;
    border: 1px solid {p['--border']} !important;
}}
li[role="option"] {{
    background: {p['--bg-card']} !important;
    color: {p['--text-primary']} !important;
}}
li[role="option"]:hover, li[aria-selected="true"] {{
    background: {p['--bg-hover']} !important;
}}

/* ── Expanders ── */
[data-testid="stExpander"] {{
    background: {p['--bg-card']} !important; border: 1px solid {p['--border']} !important; border-radius: 8px !important;
}}
[data-testid="stExpander"] summary {{ color: {p['--text-primary']} !important; }}

/* ── Dataframes / Tables ── */
[data-testid="stDataFrame"] {{ border: 1px solid {p['--border']} !important; border-radius: 8px !important; overflow: hidden; }}
[data-testid="stDataFrame"] > div,
[data-testid="stDataFrame"] > div > div {{
    background: {p['--table-bg']} !important;
    color: {p['--text-primary']} !important;
}}
/* iframe that contains the GlideDataGrid */
[data-testid="stDataFrame"] iframe {{
    background: {p['--table-bg']} !important;
    color-scheme: {"light" if p["--bg-app"].startswith("#f") else "dark"};
}}
/* st.table() HTML table */
table {{ border-collapse: collapse; width: 100%; }}
thead tr {{ background: {p['--bg-card2']} !important; }}
thead th {{
    background: {p['--bg-card2']} !important;
    color: {p['--text-muted']} !important;
    border-bottom: 1px solid {p['--border']} !important;
    padding: 8px 12px; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.5px;
}}
tbody tr {{ background: {p['--bg-card']} !important; }}
tbody tr:nth-child(even) {{ background: {p['--bg-card2']} !important; }}
tbody td {{
    background: inherit !important;
    color: {p['--text-primary']} !important;
    border-bottom: 1px solid {p['--border']} !important;
    padding: 7px 12px;
}}

/* ── Alerts ── */
[data-testid="stAlert"] {{ border-radius: 8px !important; }}
hr {{ border-color: {p['--border']} !important; opacity: 0.5; }}

/* ── Sliders, checkboxes, radio ── */
[data-testid="stSlider"] [data-baseweb="slider"] div[role="slider"] {{
    background: {p['--accent']} !important; border-color: {p['--accent']} !important;
}}
[data-testid="stCheckbox"] label, [data-testid="stRadio"] label {{ color: {p['--text-primary']} !important; }}
[data-testid="stNumberInput"] input {{
    background: {p['--bg-input']} !important; color: {p['--text-primary']} !important; border-color: {p['--border']} !important;
}}
[data-baseweb="tag"] {{ background: {p['--bg-active']} !important; color: {p['--accent']} !important; }}
[data-testid="stForm"] {{
    background: {p['--bg-card']} !important; border: 1px solid {p['--border']} !important; border-radius: 10px; padding: 16px;
}}

/* ── Markdown info boxes ── */
[data-testid="stMarkdownContainer"] {{ color: {p['--text-primary']} !important; }}
"""
