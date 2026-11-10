import io
import json
import copy
import bisect
import config as c 
import pandas as pd
import yfinance as yf
import streamlit as st
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker

from curl_cffi import requests
from datetime import datetime as dt
from datetime import timedelta as td
from streamlit.components.v1 import html
from utils.logger import log_event

# session is required to avoid 429s from yfinance
# I believe yfinance rate limits based on User-Agent header
# which, without this session, is set to python-requests
session = requests.Session(impersonate="chrome")

def _combine_prices(base, fresh):
    """Overlay `fresh` prices onto `base` by date. Fresh values win; `base` fills any gaps."""
    if base.empty:
        return fresh.copy()
    combined = fresh.set_index("Date").combine_first(base.set_index("Date"))
    return combined.rename_axis("Date").reset_index()

def _find_rewritten_tickers(cached, fresh):
    """
        Tickers whose cached closes no longer match a fresh download of the same days.

        Yahoo adjusts every earlier close after a split or dividend, so a mismatch on
        any overlapping day means that ticker's whole cached history is out of date.
    """
    if cached.empty or fresh.empty:
        return set()
    cached = cached.set_index("Date")
    cached = cached[~cached.index.duplicated(keep="last")]
    fresh = fresh.set_index("Date")
    dates = cached.index.intersection(fresh.index)
    tickers = cached.columns.intersection(fresh.columns)
    if dates.empty or tickers.empty:
        return set()
    old = cached.loc[dates, tickers].apply(pd.to_numeric, errors="coerce")
    new = fresh.loc[dates, tickers].apply(pd.to_numeric, errors="coerce")
    drift = (new / old - 1).abs()
    return set(drift.columns[(drift > c.PRICE_ADJUSTMENT_TOLERANCE).any()])

def load_app_state():
    """ load trades & stock data into session state. """

    def _download_close(tickers, start_date, end_date=None):
        """ Pull daily close prices from start_date through end_date (default: today). """
        end_date = end_date or today
        if not tickers or start_date > end_date:
            return pd.DataFrame()
        close = yf.download(
            sorted(tickers),
            start=start_date,
            end=end_date + td(days=1),
            interval="1d",
            session=session,
            progress=False,
        )["Close"]
        if close.empty:
            return pd.DataFrame()
        if isinstance(close, pd.Series):
            close = close.to_frame(name=sorted(tickers)[0])
        close = close.reset_index()
        if "Date" not in close.columns:
            close = close.rename(columns={close.columns[0]: "Date"})
        close["Date"] = pd.to_datetime(close["Date"]).dt.normalize()
        return close

    if "trades" not in st.session_state:
        user = st.session_state.user
        try:
            response = c.s3.get_object(Bucket=c.S3_BUCKET, Key=user.TRADES_JSON_PATH)
            trades_str = response['Body'].read().decode('utf-8')
            st.session_state["trades"] = json.loads(trades_str)
        except c.s3.exceptions.NoSuchKey:
            # New user with no saved trades yet; seed with defaults.
            # copy.deepcopy avoids mutating the module-level DEFAULT_TRADES constant.
            st.session_state["trades"] = copy.deepcopy(c.DEFAULT_TRADES)

        # Backfill `source` for trades persisted before the field existed so the
        # editor and downstream filters can rely on the column being present.
        # Uses [] to match the ListColumn shape used for tags.
        for trade in st.session_state["trades"]:
            trade.setdefault("source", [])

        st.session_state["tickers"] = set(trade["ticker"] for trade in st.session_state["trades"])
        st.session_state["tickers_by_tags"] = {tag: set([trade["ticker"] for trade in st.session_state["trades"] if tag in trade.get("tags", [])]) for tag in set(tag for trade in st.session_state["trades"] for tag in trade.get("tags", []))}
        st.session_state["tickers_by_source"] = {
            source: set(
                trade["ticker"]
                for trade in st.session_state["trades"]
                if source in trade.get("source", [])
            )
            for source in set(
                source
                for trade in st.session_state["trades"]
                for source in trade.get("source", [])
            )
        }

        st.toast(f"""Trading history loaded!  
            Monitoring {len(st.session_state['trades'])} trades across {len(st.session_state['tickers'])} tickers.
        """)

    if "ticker_data" not in st.session_state:
        try:
            ticker_data_obj = c.s3.get_object(Bucket=c.S3_BUCKET, Key=st.session_state.user.TICKER_DATA_PATH)
            st.session_state["ticker_data"] = pd.read_parquet(io.BytesIO(ticker_data_obj['Body'].read()))
        except c.s3.exceptions.NoSuchKey:
            st.session_state["ticker_data"] = pd.DataFrame()

        # Work on a copy so state can be updated atomically after refresh logic.
        ticker_data = st.session_state["ticker_data"].copy()
        required_tickers = st.session_state.get("tickers", set())
        required_tickers.add(c.MARKET)  # Always ensure market data is included for comparisons.
        today = dt.now().date()

        # Normalize legacy parquet shapes where Date was saved as index.
        if not ticker_data.empty and "Date" not in ticker_data.columns:
            ticker_data = ticker_data.reset_index()
            if "Date" not in ticker_data.columns and "index" in ticker_data.columns:
                ticker_data = ticker_data.rename(columns={"index": "Date"})

        # Derive current coverage window and known ticker columns.
        if "Date" in ticker_data.columns and not ticker_data.empty:
            ticker_data["Date"] = pd.to_datetime(ticker_data["Date"]).dt.normalize()
            latest_date = ticker_data["Date"].max().date()
            earliest_date = ticker_data["Date"].min().date()
            existing_tickers = {col for col in ticker_data.columns if col != "Date"}
        else:
            latest_date = None
            earliest_date = None
            existing_tickers = set()
            if ticker_data.empty:
                ticker_data = pd.DataFrame(columns=["Date"])

        # Prices are needed from the earliest trade onward.
        trades = st.session_state.get("trades", [])
        history_start = min(
            (dt.strptime(trade["date"], c.DATES_FORMAT).date() for trade in trades),
            default=today,
        )

        # Figure out what needs to be fetched:
        # 1) tickers that are present in trades but missing from stored parquet columns
        # 2) days before the cached window, when a trade predates it (e.g. an older trade added later)
        # 3) newer dates after the latest cached row
        missing_tickers = required_tickers - existing_tickers
        cached_tickers = required_tickers & existing_tickers
        needs_older_history = earliest_date is not None and history_start < earliest_date
        needs_date_refresh = latest_date is None or latest_date < today

        if missing_tickers or needs_older_history or needs_date_refresh:
            toast_lines = ["Refreshing stock data for..."]
            if missing_tickers:
                toast_lines.append(f"{len(missing_tickers)} new tickers")
            if needs_older_history:
                toast_lines.append(f"history back to {history_start}")
            if needs_date_refresh:
                toast_lines.append(
                    f"new data since {latest_date or earliest_date or 'latest trade date.'}"
                )
            toast_msg = "  \n".join(toast_lines)
            st.toast(toast_msg)
        else:
            st.toast("Cached stock data loaded. No refresh needed.")

        updated = False
        # First day the cache must cover once this refresh is done.
        coverage_start = min(history_start, earliest_date) if earliest_date is not None else history_start

        # Backfill missing ticker columns across the full date range so all symbols
        # share the same historical timeline. On a cold start this is every ticker.
        if missing_tickers:
            missing_data = _download_close(missing_tickers, coverage_start)
            if not missing_data.empty:
                ticker_data = _combine_prices(ticker_data, missing_data)
                updated = True

        # Extend cached tickers back to the earliest trade.
        if needs_older_history and cached_tickers:
            older_data = _download_close(cached_tickers, history_start, end_date=earliest_date - td(days=1))
            if not older_data.empty:
                ticker_data = _combine_prices(ticker_data, older_data)
                updated = True

        # Fetch dates after the last cached one. The download starts a few days early so
        # already-cached days can be checked against what Yahoo reports for them now.
        if needs_date_refresh and cached_tickers:
            refresh_start = latest_date - td(days=c.PRICE_REFRESH_OVERLAP_DAYS)
            refresh_data = _download_close(cached_tickers, refresh_start)
            if not refresh_data.empty:
                # A split or dividend since the last refresh rewrites every earlier close.
                # Appending to the old history would show a fake jump at that date (about
                # -90% for a 10-for-1 split), so re-download those tickers' full history.
                rewritten = _find_rewritten_tickers(ticker_data, refresh_data)
                if rewritten:
                    full_history = _download_close(rewritten, coverage_start)
                    if not full_history.empty:
                        ticker_data = _combine_prices(ticker_data.drop(columns=sorted(rewritten)), full_history)
                        st.toast(f"Re-downloaded price history for {', '.join(sorted(rewritten))} after a split or dividend.")

                ticker_data = _combine_prices(ticker_data, refresh_data)
                updated = True

        # Clean up stale tickers that are no longer in trades.
        if "Date" in ticker_data.columns and not ticker_data.empty:
            ticker_data["Date"] = pd.to_datetime(ticker_data["Date"]).dt.normalize()
            ticker_data = ticker_data.sort_values("Date")

            stale_tickers = [
                col for col in ticker_data.columns if col != "Date" and col not in required_tickers
            ]
            if stale_tickers:
                ticker_data = ticker_data.drop(columns=stale_tickers)
                updated = True

        # Persist only when there are changes: normalize dates, keep latest row per day,
        # then write both session state and parquet in S3.
        if updated:
            ticker_data["Date"] = pd.to_datetime(ticker_data["Date"]).dt.normalize()
            ticker_data = ticker_data.sort_values("Date").drop_duplicates(subset=["Date"], keep="last")
            ticker_data = ticker_data.reset_index(drop=True)
            st.session_state["ticker_data"] = ticker_data

            st.toast("Cached stock data updated.")

            buffer = io.BytesIO()

            # only cache data up to previous day 
            # this avoids writing non-final ticker data for the current day; for when app is used intraday before close
            ticker_data[ticker_data["Date"].dt.date < today].to_parquet(buffer, index=False) 
            c.s3.put_object(
                Bucket=c.S3_BUCKET,
                Key=st.session_state.user.TICKER_DATA_PATH,
                Body=buffer.getvalue(),
                ContentType='application/octet-stream'
            )

def save_trades(edited_trades):
    """Save edited trades DataFrame to S3 as JSON."""
    # Set date format when saving to json
    # without this step, date data is saved as unix ms
    edited_trades["date"] = edited_trades["date"].dt.strftime(c.DATES_FORMAT)

    # if trades.json saves empty tags as None
    # then streamlit ListColumn fails to properly recognize newly added elements
    # to avoid this, force save empty tags as an empty array
    edited_trades["tags"] = edited_trades["tags"].apply(lambda x: [] if x is None else x)

    # source is also a ListColumn, so apply the same None -> [] normalization
    # to keep the persisted JSON shape consistent across all trades.
    if "source" in edited_trades.columns:
        edited_trades["source"] = edited_trades["source"].apply(lambda x: [] if x is None else x)
    else:
        edited_trades["source"] = [[] for _ in range(len(edited_trades))]

    # ensure tickers are uppercase strings
    edited_trades['ticker'] = edited_trades['ticker'].astype(str).str.upper()

    # Convert DataFrame to JSON string using a buffer
    json_buffer = io.StringIO()
    edited_trades.to_json(json_buffer, orient="records", indent=4)

    # Upload to S3
    c.s3.put_object(
        Bucket=c.S3_BUCKET,
        Key=st.session_state.user.TRADES_JSON_PATH,
        Body=json_buffer.getvalue(),
        ContentType='application/json'
    )

    # notify about user trade activity
    log_event("trades_synced", st.session_state.user, count=len(edited_trades))
    c.po.send_notification(f"{st.session_state.user} synced {len(edited_trades)} trades.")

    # Update session state after successful save
    del st.session_state["trades"]
    del st.session_state["ticker_data"]
    st.rerun()

def generate_results(tagged_trades):
    res = st.session_state["ticker_data"].sort_values("Date", ignore_index=True)
    
    # trim res to only include dates from 30 days before the earliest trade to today
    if tagged_trades:
        earliest_date = min(dt.strptime(trade["date"], c.DATES_FORMAT).date() for trade in tagged_trades) - td(days=c.NUM_DAYS_PRECEDING_ANALYSIS)
        latest_date = dt.today().date()
        res = res[(res["Date"].dt.date >= earliest_date) & (res["Date"].dt.date <= latest_date)]

    # Keep persisted cache raw; apply fill only on analysis output for chart continuity.
    price_cols = [col for col in res.columns if col != "Date"]
    if price_cols and res[price_cols].isna().values.any():
        res[price_cols] = res[price_cols].ffill()

    # group trades by the trading day they fill on
    # then add a trades column containing list of trades on a given date
    trades_map = generate_trades_map(tagged_trades, trading_days=res["Date"].dt.date.tolist())

    # track trades col in res df for trades executed on given date
    res["trades"] = res["Date"].dt.date.map(lambda d: trades_map.get(d, []))

    # Compute cumulative portfolio and market values in a single pass.
    res = calculate_cumulative_shares(res)

    return res

def color_vals(val):
    """pd styler to color cell text based on value"""
    color = "green" if val > 0 else "red"
    return f"color: {color}"

def calculate_cumulative_shares(df):
    portfolio = {}  # Running portfolio holdings by ticker.
    ticker_prices = {}  # Cached numpy views for fast per-row price reads.
    market_prices = df[c.MARKET].to_numpy(copy=False)
    trades_by_row = df["trades"].to_numpy(copy=False)

    market_shares_bought = 0.0
    total_invested = 0.0
    portfolio_values = []
    market_values = []
    invested_values = []

    for i, trades in enumerate(trades_by_row):
        market_price = market_prices[i]

        for trade in trades:
            ticker = trade["ticker"]
            amount = trade["amount"]

            if ticker not in ticker_prices and ticker in df.columns:
                ticker_prices[ticker] = df[ticker].to_numpy(copy=False)
            ticker_series = ticker_prices.get(ticker)
            ticker_price = ticker_series[i] if ticker_series is not None else None

            if (
                pd.notna(ticker_price)
                and ticker_price > 0
                and pd.notna(market_price)
                and market_price > 0
            ):
                portfolio[ticker] = portfolio.get(ticker, 0.0) + (amount / ticker_price)
                market_shares_bought += amount / market_price
                total_invested += amount

        portfolio_value = 0.0
        for ticker, qty in portfolio.items():
            price = ticker_prices[ticker][i]
            if pd.notna(price):
                portfolio_value += qty * price

        portfolio_values.append(portfolio_value)
        market_values.append(market_shares_bought * market_price)
        invested_values.append(total_invested)

    df[c.STOCK_PORTFOLIO_COL_NAME] = portfolio_values
    df[c.MARKET_PORTFOLIO_COL_NAME] = market_values
    df["total_invested"] = invested_values

    return df

def generate_trades_map(trades, trading_days=None):
    """
        Groups trades by the day they fill on.

        Price rows only exist for trading days. With `trading_days` (sorted dates that
        have prices), a trade dated on a weekend or market holiday fills at the next
        trading day's close. A trade with no trading day within
        c.MAX_DAYS_TO_NEXT_TRADING_DAY of its date (e.g. one dated in the future) is
        left out, since it can't be priced yet.
    """
    trades_map = {}
    for trade in trades:
        date = dt.strptime(trade["date"], c.DATES_FORMAT).date()
        if trading_days is not None:
            i = bisect.bisect_left(trading_days, date)
            if i == len(trading_days) or (trading_days[i] - date).days > c.MAX_DAYS_TO_NEXT_TRADING_DAY:
                continue
            date = trading_days[i]
        if date not in trades_map:
            trades_map[date] = []
        trades_map[date].append(trade)

    return trades_map

def plot_results(res, show_as_pct=False):
    fig, ax = plt.subplots(figsize=(12, 6))

    portfolio = res["portfolio_value"]
    market = res["market_value"]
    invested = res["total_invested"]

    if show_as_pct:
        portfolio = portfolio.where(invested > 0, 0.0)
        market = market.where(invested > 0, 0.0)
        invested_safe = invested.replace(0, 1)  # avoid division by zero; numerator is already 0
        portfolio = (portfolio - invested) / invested_safe * 100
        market = (market - invested) / invested_safe * 100

    ax.plot(res['Date'], portfolio, label=c.STOCK_PORTFOLIO_LABEL)
    ax.plot(res['Date'], market, label=c.MARKET_PORTFOLIO_LABEL)
    if not show_as_pct:
        ax.plot(res['Date'], invested, label="Total Invested", linestyle="--", color="gray")

    # Add annotations for trades
    for i, row in res.iterrows():
        if row.get("trades"):
            notes = ", ".join([f"{t['ticker']}" for t in row["trades"]])
            y_pos = portfolio.loc[i]
            ax.annotate(
                notes,
                xy=(row["Date"], y_pos),
                xytext=(0, 10),
                textcoords="offset points",
                fontsize=8,
                arrowprops=dict(arrowstyle="->", color="gray", relpos=(1, 0)),
                ha='right'
            )


    # axis formatting
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d, %Y'))
    if show_as_pct:
        ax.yaxis.set_major_formatter(mticker.StrMethodFormatter('{x:,.1f}%'))
        ax.set_ylabel('Return (%)')
    else:
        ax.yaxis.set_major_formatter(mticker.StrMethodFormatter('${x:,.0f}'))
        ax.set_ylabel('Portfolio Value ($)')
    plt.setp(ax.get_xticklabels(), rotation=45)

    # Formatting
    ax.legend()
    ax.grid(True)

    return fig

def trades_breakdown(trades):
    """
        Builds the per-trade breakdown shown in the winning/losing metric popovers.

        Each trade is a dict with "date", "ticker" and "excess_return" keys.
        Returns a DataFrame sorted newest first, or None when there are no
        trades so the caller can skip the popover entirely.
    """
    if not trades:
        return None

    breakdown = pd.DataFrame(trades)
    breakdown["date"] = pd.to_datetime(breakdown["date"], format=c.DATES_FORMAT)

    return breakdown.sort_values(by="date", ascending=False, ignore_index=True)

def get_metrics(res):
    metrics = []

    # calculate trades metadata
    trading_days = res[res["trades"].notna() & res["trades"].astype(bool)]
    latest_date = res.iloc[-1]

    winners = []
    losers = []
    total_invested = 0
    trades_summary = []
    for _, row in trading_days.iterrows():
        market_purchase_price = row[c.MARKET]
        for trade in row["trades"]:
            total_invested += trade["amount"]

            purchase_price = row[trade["ticker"]]
            latest_price = latest_date[trade["ticker"]]
            latest_market_price = latest_date[c.MARKET]

            trade_return = (latest_price - purchase_price)/purchase_price
            market_return = (latest_market_price - market_purchase_price)/market_purchase_price

            trades_summary.append({
                "ticker": trade["ticker"],
                "date": trade["date"],
                "amount": trade["amount"],
                "purchase_price": purchase_price,
                "latest_price": latest_price,
                "return": trade_return,
                "market_return": market_return
            })

            # A trade only wins if it beat what the same money would have earned
            # in the market over the identical holding period.
            excess_return = trade_return - market_return
            classified = {
                "date": trade["date"],
                "ticker": trade["ticker"],
                "excess_return": excess_return,
            }
            if excess_return > 0:
                winners.append(classified)
            else:
                losers.append(classified)

    # metrics: number of winning/losing trades
    beat_market_help = f"A trade wins when its return beats {c.MARKET} over the same holding period."
    metrics.append({
        "label": "Winning Trades", 
        "value": len(winners),
        "help": beat_market_help if winners else f"No winners! 😞\n\n{beat_market_help}",
        "trades": trades_breakdown(winners),
    })

    metrics.append({
        "label": "Losing Trades", 
        "value": len(losers),
        "help": beat_market_help if losers else f"No losers! 🎉\n\n{beat_market_help}",
        "trades": trades_breakdown(losers),
    })

    # metric: success rate
    total_trades = res["trades"].apply(len).sum()
    winning_percentage = len(winners) / total_trades * 100 if total_trades else 0
    metrics.append({
        "label": "Success Rate",
        "value": f"{winning_percentage:.0f}%",
        "help": f"Share of trades that outperformed {c.MARKET} over the same holding period."
    })

    # metric: number of trades
    metrics.append({
        "label": "Total Trades", 
        "value": total_trades,
        "help": "All trades are included except when filters are applied."
    })

    # metric: total invested
    metrics.append({"label": "Total Invested", "value": f"${total_invested:,.0f}"})

    # metrics: final portfolio values
    final_stock_value = res[c.STOCK_PORTFOLIO_COL_NAME].iloc[-1]
    final_market_value = res[c.MARKET_PORTFOLIO_COL_NAME].iloc[-1]

    sign = "" if final_stock_value - total_invested >= 0 else "-"
    delta = final_stock_value - total_invested
    delta_pct = (delta / total_invested * 100) if total_invested != 0 else 0
    metrics.append({
        "label": f"{c.STOCK_PORTFOLIO_LABEL} Value",
        "value": f"${final_stock_value:,.0f}",
        "delta": f"{sign}${abs(delta):,.0f} | {sign}{abs(delta_pct):.2f}%"
    })

    sign = "" if final_market_value - total_invested >= 0 else "-"
    delta = final_market_value - total_invested
    delta_pct = (delta / total_invested * 100) if total_invested != 0 else 0
    metrics.append({
        "label": f"{c.MARKET_PORTFOLIO_LABEL} Value", 
        "value": f"${final_market_value:,.0f}",
        "delta": f"{sign}${abs(delta):,.0f} | {sign}{abs(delta_pct):.2f}%"
    })
    
    return metrics, trades_summary

def validate_changes(edited_trades):
    """
        runs a series of validations
        returns True if any validation fails alongside an error message
        else returns False, None
    """

    # allow tags and source to be empty
    # remaining cols must be filled
    optional_cols = {"tags", "source"}
    required_cols = [col for col in edited_trades.columns if col not in optional_cols]
    if edited_trades[required_cols].isnull().values.any():
        return True, "You have trades with unfinished details."
    if (edited_trades["amount"].apply(lambda a: not isinstance(a, (int, float)) or a <= 0)).any():
        return True, "Amounts must be valid positive numbers."

    return False, None

def get_tags(edited_trades):
    res = set()
    for tags in edited_trades["tags"]:
        if tags is not None:
            for tag in tags:
                res.add(tag)

    return res

def get_sources(edited_trades):
    """Collect the union of source values across all trades for filter UI."""
    res = set()
    if "source" not in edited_trades.columns:
        return res
    for sources in edited_trades["source"]:
        if sources is not None:
            for source in sources:
                res.add(source)

    return res

def humanize_date(date_str):
    """
        accepts dates in YYYY-MM-DD format and returns a humanized version
        example: 2025-09-18 --> Thursday, September 18, 2025
    """
    return dt.strptime(date_str, c.DATES_FORMAT).strftime(c.PREFERRED_UI_DATE_FORMAT_DATETIME)


def render_animation(name: str, height: int = 470):
    """
    Render an HTML/CSS animation by file stem.

    These are self-contained vector animations (no GIF, no video),
    so they stay crisp at any DPI and weigh only a few KB each.

    Uses st.components.v1.html which renders in an iframe (required for isolation).
    Note: this is deprecated after 2026-06-01 in favor of st.iframe.
    I choose to still use this because the Streamlit app is pinned to an older version in deployment.
    But should be warned that this would break, alongside other uses of v1 html(), if Streamlit is updated.

    Args:
        name: file stem, e.g. "example-results"
        height: iframe height in px (default suits centered layout ~704px wide)
    """
    with open(f"{c.ASSETS_PATH}/animations/{name}.html", encoding="utf-8") as f:
        html_content = f.read()
    html(html_content, height=height, scrolling=False)
