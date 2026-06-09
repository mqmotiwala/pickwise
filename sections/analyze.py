import gc
import time
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

import config as c
import utils.css as css
import utils.helpers as h


def show_analyze():
    """
    Renders the Analyze Trades section: tag/source/ticker filters, summary
    metrics, results table, chart, and CSV download. Reads the live edits
    stashed by the trades section to derive available filter options.
    """

    css.header(css.underline("Analyze Trades"), lvl=5)

    edited_trades = st.session_state.get("edited_trades")
    if edited_trades is None:
        # show_trades() should run first; if it didn't, there's nothing to analyze.
        return

    tags = h.get_tags(edited_trades)
    pills_label_action = "Create" if len(tags) == 0 else "Choose"
    selected_tag = st.pills(
        label=f"{pills_label_action} tags to selectively analyze trading portfolios.",
        options=sorted(tags, key=str.lower),
        selection_mode="single",
        default=None,
    )

    sources = h.get_sources(edited_trades)
    sources_label_action = "Create" if len(sources) == 0 else "Choose"
    selected_source = st.pills(
        label=f"{sources_label_action} sources to selectively analyze trading portfolios.",
        options=sorted(sources, key=str.lower),
        selection_mode="single",
        default=None,
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
        options=sorted(ticker_options),
        index=None,
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
                    label=metric["label"],
                    value=metric["value"],
                    delta=metric.get("delta", None),
                    help=metric.get("help", None),
                )

        st.dataframe(
            pd.DataFrame(trades_summary).sort_values(by="date", ascending=False, ignore_index=True).style.applymap(h.color_vals, subset=["return", "market_return"]),
            column_config=c.COLUMN_CONFIGS,
        )

        st.markdown("")  # empty space
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
