import time
import traceback
import streamlit as st

from utils.user import User
from utils.logger import logger

# the provider name here must match the [auth.<name>] section in secrets.toml
GOOGLE_PROVIDER = "google"


def login_button(unique_key=None):
    """
    Renders a "Continue with Google" button that kicks off Streamlit's native
    OIDC login flow (st.login).

    native auth issues a signed identity cookie, 
    so the user stays logged in across full page refreshes.
    Authentication config lives in .streamlit/secrets.toml under [auth].

    Parameters:
        unique_key: Optional. A unique key to avoid Streamlit duplicate-element
        errors when the button is rendered more than once on a page.
    """
    if unique_key is not None and not isinstance(unique_key, str):
        unique_key = str(unique_key)

    clicked = st.button(
        "Continue with Google",
        icon=":material/login:",
        use_container_width=True,
        type="primary",
        key=unique_key,
    )

    if clicked:
        # st.login triggers a redirect to the identity provider. On return,
        # Streamlit sets the identity cookie and st.user.is_logged_in becomes True.
        st.login(GOOGLE_PROVIDER)


def ensure_user_loaded():
    """
    Rebuilds the in-memory User object from the persistent identity cookie.

    Streamlit keeps the user authenticated across page refreshes via a signed
    cookie (exposed as st.user.is_logged_in). Session state, however, is cleared
    on every refresh, so the User object must be reconstructed from the st.user
    OIDC claims whenever it is missing from session state.

    Callers should only invoke this when st.user.is_logged_in is True.
    """
    if st.session_state.get("user") is not None:
        return

    try:
        # st.user behaves like a mapping of OIDC claims returned by Google
        payload = {
            "sub": st.user.get("sub"),
            "email": st.user.get("email"),
            "name": st.user.get("name"),
            "given_name": st.user.get("given_name"),
            "family_name": st.user.get("family_name"),
            "picture": st.user.get("picture"),
            "iat": st.user.get("iat", int(time.time())),
        }

        st.session_state["user"] = User(payload=payload)
        st.session_state["auth"] = st.session_state.user.email

        logger.info(f"{st.session_state.user} authenticated successfully")

    except Exception as e:
        logger.error(f"An error occurred during authentication: {e}")

        st.error(f"An error occurred during authentication: {e}")
        st.text("Detailed traceback:")
        st.code(traceback.format_exc())
        st.stop()


def logout():
    logger.info(f"{st.session_state.user} logged out")

    # clear local session state first
    st.session_state.clear()

    # st.logout clears the identity cookie and queues a redirect to end the
    # session, but it does NOT halt the current script run. Stop here so the
    # rest of the authenticated page (which reads st.session_state.user) does
    # not execute against the just-cleared state before the redirect lands.
    st.logout()
    st.stop()
