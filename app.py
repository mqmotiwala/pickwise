import streamlit as st
import helpers as h
import pandas as pd
import config as c
import time

st.set_page_config(
    page_title="pickwise",
    page_icon="📈",
    layout="wide"
)

st.subheader('Stock Picking vs. ETF Investing Portfolios Over Time')

st.subheader("Trades")
st.text("""
    Configure trades to monitor here.  
    Tags can be used to run analyses on subsets of trades.
""")

trades = h.load_trades()
edited_trades = st.data_editor(
    pd.DataFrame(trades).assign(date=lambda d: pd.to_datetime(d["date"], format=c.DATES_FORMAT)), 
    num_rows="dynamic",
    column_config=c.COLUMN_CONFIGS
)

if st.button("Sync to Cloud", icon=":material/save:"):
    valid, error_msg = h.validate_changes(edited_trades)

    if valid:
         st.error(f"Save rejected. {error_msg}", icon="🚨")
    else:
        h.save_trades(edited_trades)
        st.rerun()

st.divider()

st.subheader("Analyze Trades")

tags = h.get_tags(edited_trades)
pills_label_action = "Create" if len(tags) == 0 else "Choose"
selected_tags = st.pills(
    label = f"{pills_label_action} tags to selectively analyze trading strategies.",
    options = tags,
    selection_mode = "multi",
    default = None
)

res = h.generate_results(selected_tags)

st.markdown("") # empty space
metrics, trades_summary = h.get_metrics(res)
cols = st.columns(len(metrics))
for col, metric in zip(cols, metrics):
     with col:
            st.metric(
                label = metric["label"], 
                value = metric["value"], 
                delta = metric.get("delta", None),
                help = metric.get("help", None)
            )

st.dataframe(
    pd.DataFrame(trades_summary).style.applymap(h.color_vals, subset=["return", "market_return"]),
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