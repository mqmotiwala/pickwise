import streamlit as st
import helpers as h
import pandas as pd
import config as c

st.set_page_config(layout="wide")

st.subheader('Stock Picking vs. ETF Investing Portfolios Over Time')
res = h.generate_results()
st.pyplot(h.plot_results(res))

st.divider()

st.subheader("Trades")
trades = h.load_trades(enabled_only=False)
metrics = h.get_metrics(res)
cols = st.columns(len(metrics))
for col, metric in zip(cols, metrics):
     with col:
            st.metric(
                label = metric["label"], 
                value = metric["value"], 
                delta = metric.get("delta", None),
                help = metric.get("help", None)
            )

edited_trades = st.data_editor(pd.DataFrame(trades), hide_index=True, num_rows="dynamic")
if st.button("💾 Save Changes"):
    valid, error_msg = h.validate_changes(edited_trades)

    if valid:
         st.error(f"Save rejected. {error_msg}", icon="🚨")
    else:
        h.save_trades(edited_trades)
        st.success(f"Trades saved.")
        st.rerun()