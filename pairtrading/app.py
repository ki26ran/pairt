import streamlit as st
import os, sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from common.market_data.provider import _load_config as _get_config

st.set_page_config(page_title="Pair Trading System", layout="wide")

PAGES = {
    "🏠 Home": "pairtrading.reports.home",
    "1️⃣ Discover Pairs": "pairtrading.reports.discover_pairs",
    "2️⃣ Optimize": "pairtrading.reports.pair_trading",
    "📊 Backtest": "pairtrading.reports.backtest",
    "3️⃣ Live Monitor": "pairtrading.live.pair_scanner",
    "⚙️ Scheduler": "pairtrading.reports.scheduler",
    "📖 Guide": "pairtrading.reports.about",
}
PAGE_ORDER = list(PAGES.keys())

if "page" not in st.session_state:
    nav_from_qp = st.query_params.get("nav")
    st.session_state.page = nav_from_qp if nav_from_qp in PAGES.values() else "pairtrading.reports.home"

st.markdown("""
<style>
    .nav-link {
        display: block;
        padding: 6px 12px;
        margin: 2px 0;
        text-decoration: none;
        color: inherit;
        font-size: 14px;
        border-radius: 4px;
    }
    .nav-link:hover {
        background: rgba(128,128,128,0.1);
    }
    .nav-link.active {
        color: #1f77b4;
        font-weight: 600;
    }
    .nav-step {
        font-size: 11px;
        color: #666;
        display: block;
        margin-left: 12px;
    }
</style>
""", unsafe_allow_html=True)

_cfg = _get_config()
_host = _cfg.get("host", "localhost")
_provider = _cfg.get("portfolio_providers", {}).get("__default__", "yahoo")

st.sidebar.markdown(
    "<p style='margin:6px 0;text-align:center;font-size:1.1rem;font-weight:700'>📈 PairTrading</p>"
    f"<p style='margin:0;text-align:center;font-size:11px;color:#888'>{_host} • {_provider.upper()}</p>"
    "<hr style='margin:2px 0 8px'>",
    unsafe_allow_html=True,
)

for label in PAGE_ORDER:
    module = PAGES[label]
    cls = "nav-link active" if st.session_state.page == module else "nav-link"
    st.sidebar.markdown(
        f"<a href='?nav={module}' class='{cls}' target='_self'>{label}</a>",
        unsafe_allow_html=True,
    )

try:
    exec(f"from {st.session_state.page} import show")
    show()
except Exception as e:
    st.error(f"Error: {e}")
