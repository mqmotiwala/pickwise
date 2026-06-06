import json
import base64
import traceback
import config as c
import streamlit as st

from user import User
from logger import logger
from streamlit_oauth import OAuth2Component, StreamlitOauthError


def get_auth(unique_key=None):
    """
    Displays an OAuth2 login button using the provided client configuration
    On successful login, gets an authentication token for Streamlit session state.

    Parameters:
        unique_key: Optional.
        A unique key to prevent component duplication errors in Streamlit.

        If not provided, a random key will be generated internally.
        But the generation is a function of element type and parameters,
        so if this function is invoked multiple times, it'll produce a
        "multiple component_instance elements with the same auto-generated ID" error.

        So, pass unique_key argument to avoid this
    """

    # streamlit requires key type to be str
    if not isinstance(unique_key, str):
        unique_key = str(unique_key)

    try:
        # create a button to start the OAuth2 flow
        oauth2 = OAuth2Component(c.CLIENT_ID, c.CLIENT_SECRET, c.AUTHORIZE_ENDPOINT, c.TOKEN_ENDPOINT, c.TOKEN_ENDPOINT, c.REVOKE_ENDPOINT)
        result = oauth2.authorize_button(
            name="Continue with Google",
            icon="https://www.google.com.tw/favicon.ico",
            redirect_uri=c.REDIRECT_URI,
            scope="openid email profile",
            # streamlit will raise an error if elements are duplicated without unique keys
            key=unique_key,
            extras_params={"access_type": "offline", "prompt": "select_account"},
            use_container_width=True,
            pkce='S256',
        )

        if result:
            token = result["token"]
            st.session_state["token"] = token

            # decode the id_token jwt and get the user's email address
            id_token = result["token"]["id_token"]
            payload = id_token.split(".")[1]
            # add padding to the payload if needed
            payload += "=" * (-len(payload) % 4)
            payload = json.loads(base64.b64decode(payload))

            # instantiate User object
            st.session_state["user"] = User(payload=payload)
            st.session_state["auth"] = st.session_state.user.email

            logger.info(f"{st.session_state.user} authenticated successfully")

            # rerun the app to reflect the new state
            st.rerun()

    except StreamlitOauthError as e:
        # user cancelled the OAuth2 flow
        logger.warning("OAuth2 flow was cancelled by user attempting to log in.")
        pass

    except Exception as e:
        logger.error(f"An error occurred during authentication: {e}")

        st.error(f"An error occurred during authentication: {e}")
        st.text("Detailed traceback:")
        st.code(traceback.format_exc())
        st.stop()


def logout():
    logger.info(f"{st.session_state.user} logged out")

    # effectively resets the session
    st.session_state.clear()

    # rerun the app to reflect the new state
    st.rerun()
