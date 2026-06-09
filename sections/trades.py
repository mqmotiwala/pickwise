import pandas as pd
import streamlit as st

import config as c
import utils.css as css
import utils.helpers as h


def show_trades():
    """
    Renders the Add Trades section: explanatory copy, the editable trades
    table, and the cloud-sync button. Stashes the live edits in session
    state so downstream sections (e.g. analyze) can read them.
    """

    css.header(css.underline("Add Trades"), lvl=5)
    st.text("""
        Configure trades to monitor here. 
        Source and Tags can be used to add metadata by which you can focus your analyses.   
    """)

    h.load_app_state()

    trades = st.session_state.get("trades", [])
    if trades:
        trades_df = pd.DataFrame(trades).assign(
                date=lambda d: pd.to_datetime(d["date"], format=c.DATES_FORMAT)
            ).sort_values(
                by="date",
                ascending=False,
                ignore_index=True
            )
    else:
        # No trades yet (e.g. a new user). Build an empty frame with the expected
        # schema so the data editor and downstream date handling don't break.
        trades_df = pd.DataFrame(columns=c.TRADES_COLUMNS)
        trades_df["date"] = pd.to_datetime(trades_df["date"], format=c.DATES_FORMAT)

    edited_trades = st.data_editor(
        trades_df,
        num_rows="dynamic",
        column_config=c.COLUMN_CONFIGS,
    )

    if st.button("Sync to Cloud", icon=":material/save:", type="primary"):
        valid, error_msg = h.validate_changes(edited_trades)

        if valid:
            st.error(f"Save rejected. {error_msg}", icon="🚨")
        else:
            st.toast("Saved changes!", icon="💾")
            h.save_trades(edited_trades)

    # expose live edits for downstream sections in the same script run
    st.session_state["edited_trades"] = edited_trades

    css.divider()
