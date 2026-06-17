import streamlit as st

import utils.auth as a
from sections.header import show_header
from sections.landing import show_landing
from sections.trades import show_trades
from sections.analyze import show_analyze

# gate the app behind Google sign-in. Native auth keeps the user logged in
# across refreshes via a signed identity cookie (st.user.is_logged_in).
if not st.user.is_logged_in:
    # centered layout suits the landing; set inside the branch so Streamlit
    # transitions centered -> wide more seamlessly once logged in
    st.set_page_config(page_title="Pickwise", page_icon="🤑", layout="centered")
    show_landing()
    st.stop()

st.set_page_config(page_title="Pickwise", page_icon="🤑", layout="wide")

# session state is cleared on every refresh, but the identity cookie persists;
# rebuild the User object from the st.user claims before any page reads it
a.ensure_user_loaded()

show_header()
show_trades()
show_analyze()
