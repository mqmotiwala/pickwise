import config as c
import utils.css as css
import utils.auth as a
import streamlit as st


def show_landing():
    """
    Renders the logged-out landing experience: a pitch focused on what
    Pickwise does (benchmarking picks against the market), an example
    output to make it intuitive, and the Google sign-in button.
    """

    css.header(f"Pickwise 🤑")
    css.header("Evaluate stock picking portfolios against the market.", lvl=6, underline_text=False)

    explanation_text = f"""
    Pickwise let's you score your trading activity against simply investing the broad market ETFs.   
    Track your portfolio performance with views like this.  
    """
    css.markdown(f"{explanation_text}")
    st.image(f"{c.ASSETS_PATH}/example-results.png")

    a.login_button(unique_key="landing_login")
