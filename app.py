import streamlit as st
import helpers as h
import pandas as pd
import config as c
import css as css
import auth as a
import time
import gc
import matplotlib.pyplot as plt

from landing import show_landing
from streamlit_js_eval import streamlit_js_eval

# gate the app behind Google sign-in
if "auth" not in st.session_state:
    # centered layout suits the landing; set inside the branch so Streamlit
    # transitions centered -> wide more seamlessly once logged in
    st.set_page_config(page_title="Pickwise", page_icon="🤑", layout="centered")
    show_landing()
    st.stop()

st.set_page_config(page_title="Pickwise", page_icon="🤑", layout="wide")

with st.container(gap=None):
    with st.container(horizontal=True, horizontal_alignment="right"):
        css.markdown(f"## {css.highlight("Pickwise", tilt=-2.5)} 🤑")
        if st.button(key=c.LOGOUT_BUTTON_KEY_NAME, label="Logout"):
            a.logout()
    css.markdown("##### Evaluate stock picking portfolios against the market.")
    css.divider()

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

try:
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

except ValueError:
    css.divider()

    st.warning(
        """
        Heads up, adding tags or sources to new trades in one step is not allowed.    
        The recommended flow is:  
        1. Add new trades 
        2. Sync changes to the cloud
        3. _Then_ apply tags or sources once the trade is logged.
        """
        , icon="⚠️"
    )

    with st.expander("But... why?"):
        st.subheader("Why does this limitation exist?")
        st.markdown("""
            Pickwise stores the table data in Pandas before passing it to Apache Arrow,  
            which handles syncing between Python and the browser.  

            Arrow requires ListColumns (like the Tags and Source columns) to declare an inner type (e.g. `list<string>`).  
            Pandas can only mark these columns as `object`, so Arrow infers the type from the first values it sees.  
                    
            When a new row is created, the cell starts as `[]`, which is an empty, typeless list; so Arrow can't serialize it.  
        """)

        st.subheader("Why does the recommended flow work?")
        st.markdown("""
            When editing tags or sources on existing trades, Arrow infers `list<string>` from previous trades (other rows in the same column).  
        """)

    if st.button("OK, I understand."):
        streamlit_js_eval(js_expressions="parent.window.location.reload()")

    # allows the rest of the app to be rendered
    edited_trades = trades_df

css.divider()

css.header(css.underline("Analyze Trades"), lvl=5)

tags = h.get_tags(edited_trades)
pills_label_action = "Create" if len(tags) == 0 else "Choose"
selected_tag = st.pills(
    label = f"{pills_label_action} tags to selectively analyze trading portfolios.",
    options = sorted(tags, key=str.lower),
    selection_mode = "single",
    default = None
)

sources = h.get_sources(edited_trades)
sources_label_action = "Create" if len(sources) == 0 else "Choose"
selected_source = st.pills(
    label = f"{sources_label_action} sources to selectively analyze trading portfolios.",
    options = sorted(sources, key=str.lower),
    selection_mode = "single",
    default = None
)

# Ticker options narrow based on whichever filters are active.
# Both filters set -> intersection; only one set -> that filter's tickers;
# neither set -> all known tickers.
tickers_by_tags = st.session_state.get("tickers_by_tags", {})
tickers_by_source = st.session_state.get("tickers_by_source", {})
if selected_tag and selected_source:
    ticker_options = tickers_by_tags.get(selected_tag, set()) & tickers_by_source.get(selected_source, set())
elif selected_tag:
    ticker_options = tickers_by_tags.get(selected_tag, set())
elif selected_source:
    ticker_options = tickers_by_source.get(selected_source, set())
else:
    ticker_options = st.session_state.get("tickers", set())

selected_ticker = st.selectbox(
    label="Optionally, filter for a specific ticker traded within selected tag/source.",
    options = sorted(ticker_options),
    index = None,
)

if selected_ticker: 
    # if a ticker is selected, filter just for that ticker's trades
    tagged_trades = [
            trade for trade in st.session_state.get("trades", [])
            if trade.get("ticker", None) == selected_ticker
        ]
else:  
    # otherwise, intersect the selected tag and source filters; if neither is set, use all trades
    tagged_trades = st.session_state.get("trades", [])
    if selected_tag:
        tagged_trades = [
            trade for trade in tagged_trades
            if selected_tag in trade.get("tags", [])
        ]
    if selected_source:
        tagged_trades = [
            trade for trade in tagged_trades
            if selected_source in trade.get("source", [])
        ]

res = h.generate_results(tagged_trades)

css.empty_space()

if not tagged_trades:
    st.warning(
        "No trades match the given filters.",
        icon="⚠️",
    )
else:
    metrics, trades_summary = h.get_metrics(res)
    with st.container(border=False, horizontal=True, gap="small", horizontal_alignment="distribute"):
        for metric in metrics:
                st.metric(
                    label = metric["label"], 
                    value = metric["value"], 
                    delta = metric.get("delta", None),
                    help = metric.get("help", None)
                )

    st.dataframe(
        pd.DataFrame(trades_summary).sort_values(by="date", ascending=False, ignore_index=True).style.applymap(h.color_vals, subset=["return", "market_return"]),
        column_config=c.COLUMN_CONFIGS
    )
                
    st.markdown("") # empty space
    show_as_pct = st.toggle("Show as % return", value=False)
    fig = h.plot_results(res, show_as_pct=show_as_pct)
    st.pyplot(fig, clear_figure=True)
    plt.close(fig)
    st.download_button(
        label="Download CSV",
        data=res.to_csv(index=False).encode("utf-8"),
        file_name=f"data_{int(time.time())}.csv",
        mime="text/csv",
        icon=":material/download:",
    )

# run garbage collection to free RAM
gc.collect()
