import streamlit as st
import helpers as h
import pandas as pd
import config as c
import css as css
import time

from streamlit_js_eval import streamlit_js_eval

st.set_page_config(
    page_title="Pickwise",
    page_icon="🤑",
    layout="wide"
)

with st.container(gap=None):
    css.markdown(f"## {css.highlight("Pickwise", tilt=-2.5)} 🤑")
    css.markdown("##### Evaluate stock picking portfolios against the market.")

    css.divider()

css.header(css.underline("Add Trades"), lvl=5)
st.text("""
    Configure trades to monitor here.  
    Tags can be used to run analyses on subsets of trades.
""")

trades = h.load_trades()
trades_df = pd.DataFrame(trades) \
    .assign(
        date=lambda d: pd.to_datetime(d["date"], 
        format=c.DATES_FORMAT)
    ) \
    .sort_values(
        by="date", 
        ascending=False,
        ignore_index=True
    )

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
            h.save_trades(edited_trades)
            st.rerun()
except ValueError:
    css.divider()

    st.warning(
        """
        Heads up, adding tags to new trades in one step is not allowed.    
        The recommended flow is:  
        1. Add new trades 
        2. Sync changes to the cloud
        3. _Then_ apply tags once the trade is logged.
        """
        , icon="⚠️"
    )

    with st.expander("But... why?"):
        st.subheader("Why does this limitation exist?")
        st.markdown("""
            Pickwise stores the table data in Pandas before passing it to Apache Arrow,  
            which handles syncing between Python and the browser.  

            Arrow requires ListColumns (like the Tags column) to declare an inner type (e.g. `list<string>`).  
            Pandas can only mark the tags column as `object`, so Arrow infers the type from the first values it sees.  
                    
            When a new row is created, the tags cell starts as `[]`, which is an empty, typeless list; so Arrow can't serialize it.  
        """)

        st.subheader("Why does the recommended flow work?")
        st.markdown("""
            When editing tags on existing trades, Arrow infers `list<string>` from previous trades (other rows in Tags column).  
        """)

    if st.button("OK, I understand."):
        streamlit_js_eval(js_expressions="parent.window.location.reload()")

    # allows the rest of the app to be rendered
    edited_trades = trades_df

css.divider()

css.header(css.underline("Analyze Trades"), lvl=5)

tags = h.get_tags(edited_trades)
pills_label_action = "Create" if len(tags) == 0 else "Choose"
selected_tags = st.pills(
    label = f"{pills_label_action} tags to selectively analyze trading portfolios.",
    options = sorted(tags, key=str.lower),
    selection_mode = "multi",
    default = None
)

selected_trade = st.selectbox(
    label="Or, select a single trade to review.",
    options = trades,
    index = None,
    disabled = True if not selected_tags == [] else False,
    format_func = lambda trade: f"{trade["ticker"]} on {h.humanize_date(trade["date"])}"
)

if selected_trade and not selected_tags:
    selection = [selected_trade]
else:
    if selected_tags:
        selection = [
            trade for trade in trades
            if trade.get("tags") and any(tag in trade["tags"] for tag in selected_tags)
        ]
    else:
        selection = trades

res = h.generate_results(selection)

css.empty_space()

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
st.pyplot(h.plot_results(res))
st.download_button(
    label="Download CSV",
    data=res.to_csv(index=False).encode("utf-8"),
    file_name=f"data_{int(time.time())}.csv",
    mime="text/csv",
    icon=":material/download:",
)