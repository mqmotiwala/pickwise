import config as c
import utils.css as css
import utils.auth as a
import streamlit as st


def show_header():
    """Top of the authenticated app: title row with logout button + tagline."""
    with st.container(gap=None):
        with st.container(horizontal=True, horizontal_alignment="distribute"):
            css.markdown(f"## {css.highlight("Pickwise", tilt=-2.5)} 🤑")
            if st.button(key=c.LOGOUT_BUTTON_KEY_NAME, label="Logout"):
                a.logout()
        css.markdown("##### Evaluate stock picking portfolios against the market.")
        css.divider()
