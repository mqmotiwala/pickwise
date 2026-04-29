import hmac
import streamlit as st
import config as c


def require_login():
    """
    Gate the app behind a simple password check.
    Reads the expected password from the APP_PASSWORD env var.
    Uses session state so the user only has to enter it once per session.
    """
    if st.session_state.get("authenticated"):
        return

    st.title("🤑 Pickwise")
    st.markdown("#### This app is private.")
    st.markdown("###### Enter the password to continue")

    password = st.text_input("Password", type="password")

    if st.button("Sign in", type="primary"):
        if hmac.compare_digest(password, c.APP_PASSWORD):
            st.session_state["authenticated"] = True
            c.po.send_notification("Successful Pickwise login!")
            st.rerun()
        else:
            c.po.send_notification("Attempted Pickwise login!")
            st.error("Incorrect password.", icon="🚫")

    st.stop()
