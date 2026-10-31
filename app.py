# PegGuard: stablecoin reserve stress tester.
# Run with: streamlit run app.py

import streamlit as st

st.set_page_config(page_title="PegGuard: Stablecoin Stress Tester", page_icon=":material/shield:",
                   layout="wide", initial_sidebar_state="expanded")

from app_pages.common import init_state, sidebar  # noqa: E402

init_state()
st.logo("assets/logo.svg", icon_image="assets/icon.svg", size="large")

# pages grouped by what the user is trying to do
pages = {
    "Analyse": [
        st.Page("app_pages/stress_lab.py", title="Stress Lab", icon=":material/science:", default=True),
        st.Page("app_pages/treasury.py", title="Treasury Planner", icon=":material/account_balance:"),
    ],
    "Compare": [
        st.Page("app_pages/overview.py", title="Dashboard", icon=":material/dashboard:"),
        st.Page("app_pages/reserve_quality.py", title="Issuer Profile", icon=":material/fact_check:"),
    ],
    "About the model": [
        st.Page("app_pages/validation.py", title="Validation", icon=":material/rule:"),
        st.Page("app_pages/methodology.py", title="Methodology", icon=":material/menu_book:"),
    ],
}
nav = st.navigation(pages)
sidebar()
nav.run()
