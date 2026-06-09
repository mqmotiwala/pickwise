import streamlit as st

from sections.header import show_header
from sections.landing import show_landing
from sections.trades import show_trades
from sections.analyze import show_analyze

# gate the app behind Google sign-in
if "auth" not in st.session_state:
    # centered layout suits the landing; set inside the branch so Streamlit
    # transitions centered -> wide more seamlessly once logged in
    st.set_page_config(page_title="Pickwise", page_icon="🤑", layout="centered")
    show_landing()
    st.stop()

st.set_page_config(page_title="Pickwise", page_icon="🤑", layout="wide")

show_header()
show_trades()
show_analyze()
