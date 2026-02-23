import io
import json
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

# session is required to avoid 429s from yfinance
# I believe yfinance rate limits based on User-Agent header
# which, without this session, is set to python-requests
session = requests.Session(impersonate="chrome")

def load_app_state():
    """ load trades & stock data into session state. """

    def _download_close(tickers, start_date):
        """ Pull daily close prices only for required date windows. """
        if not tickers or start_date > today:
            return pd.DataFrame()
        close = yf.download(
            sorted(tickers),
            start=start_date,
            end=today + td(days=1),
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
        response = c.s3.get_object(Bucket=c.S3_BUCKET, Key=c.TRADES_JSON_PATH)
        trades_str = response['Body'].read().decode('utf-8')
        st.session_state["trades"] = json.loads(trades_str)
        st.session_state["tickers"] = set(trade["ticker"] for trade in st.session_state["trades"])

        st.toast(f"""Trades history loaded!  
            Monitoring {len(st.session_state['trades'])} trades across {len(st.session_state['tickers'])} tickers.
        """)

    if "ticker_data" not in st.session_state:
        try:
            ticker_data_obj = c.s3.get_object(Bucket=c.S3_BUCKET, Key=c.TICKER_DATA_PATH)
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

        # Figure out what needs to be fetched:
        # 1) tickers that are present in trades but missing from stored parquet columns
        # 2) newer dates after the latest cached row
        missing_tickers = required_tickers - existing_tickers
        needs_date_refresh = latest_date is None or latest_date < today

        if missing_tickers or needs_date_refresh:
            toast_lines = ["Refreshing stock data for..."]
            if missing_tickers:
                toast_lines.append(f"{len(missing_tickers)} new tickers")
            if needs_date_refresh:
                toast_lines.append(
                    f"new data since {latest_date or earliest_date or 'latest trade date.'}"
                )
            toast_msg = "  \n".join(toast_lines)
            st.toast(toast_msg)
        else:
            st.toast("Stock data loaded.")

        updated = False
        # Backfill missing ticker columns across the full available date range so
        # all symbols share the same historical timeline in one DataFrame.
        if missing_tickers:
            if earliest_date is not None:
                missing_start = earliest_date
            else:
                # If no cached data exists yet, infer start from earliest trade date.
                trades = st.session_state.get("trades", [])
                if trades:
                    missing_start = min(
                        dt.strptime(trade["date"], c.DATES_FORMAT).date() for trade in trades
                    )
                else:
                    # No trades means no historical backfill is needed.
                    missing_start = today

            missing_data = _download_close(missing_tickers, missing_start)
            if not missing_data.empty:
                # Outer merge preserves existing rows and adds new ticker columns.
                ticker_data = ticker_data.merge(missing_data, on="Date", how="outer")
                updated = True

        # Append only dates newer than the last cached date for all required tickers.
        if needs_date_refresh and required_tickers:
            if latest_date is None:
                # Cold start: build initial history from earliest trade date.
                trades = st.session_state.get("trades", [])
                if trades:
                    refresh_start = min(
                        dt.strptime(trade["date"], c.DATES_FORMAT).date() for trade in trades
                    )
                else:
                    refresh_start = today
            else:
                # Incremental refresh starts the day after the most recent cached date.
                refresh_start = latest_date + td(days=1)

            refresh_data = _download_close(required_tickers, refresh_start)
            if not refresh_data.empty:
                # Ensure schema compatibility before concat when new columns appear.
                for col in refresh_data.columns:
                    if col != "Date" and col not in ticker_data.columns:
                        ticker_data[col] = pd.NA
                ticker_data = pd.concat([ticker_data, refresh_data], ignore_index=True, sort=False)
                updated = True

        # Clean up stale tickers that are no longer in trades and forward-fill any missing values
        if "Date" in ticker_data.columns and not ticker_data.empty:
            ticker_data["Date"] = pd.to_datetime(ticker_data["Date"]).dt.normalize()
            ticker_data = ticker_data.sort_values("Date")

            stale_tickers = [
                col for col in ticker_data.columns if col != "Date" and col not in required_tickers
            ]
            if stale_tickers:
                ticker_data = ticker_data.drop(columns=stale_tickers)
                updated = True

            price_cols = [col for col in ticker_data.columns if col != "Date"]
            if price_cols and ticker_data[price_cols].isna().values.any():
                ticker_data[price_cols] = ticker_data[price_cols].ffill()
                updated = True

        # Persist only when there are changes: normalize dates, keep latest row per day,
        # then write both session state and parquet in S3.
        if updated:
            ticker_data["Date"] = pd.to_datetime(ticker_data["Date"]).dt.normalize()
            ticker_data = ticker_data.sort_values("Date").drop_duplicates(subset=["Date"], keep="last")
            ticker_data = ticker_data.reset_index(drop=True)
            st.session_state["ticker_data"] = ticker_data

            st.toast("Stock data refreshed and up to date.")

            buffer = io.BytesIO()
            ticker_data.to_parquet(buffer, index=False)
            c.s3.put_object(
                Bucket=c.S3_BUCKET,
                Key=c.TICKER_DATA_PATH,
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

    # ensure tickers are uppercase strings
    edited_trades['ticker'] = edited_trades['ticker'].astype(str).str.upper()

    # Convert DataFrame to JSON string using a buffer
    json_buffer = io.StringIO()
    edited_trades.to_json(json_buffer, orient="records", indent=4)

    # Upload to S3
    c.s3.put_object(
        Bucket=c.S3_BUCKET,
        Key=c.TRADES_JSON_PATH,
        Body=json_buffer.getvalue(),
        ContentType='application/json'
    )

    # Update session state after successful save
    del st.session_state["trades"]
    del st.session_state["ticker_data"]
    st.rerun()

def generate_results(trades):
    # get analysis start date based on earliest trade date
    ANALYSIS_START_DATE = min(
            dt.strptime(trade["date"], c.DATES_FORMAT) for trade in trades
        )- td(days=c.NUM_DAYS_PRECEDING_ANALYSIS)
    
    # generate a df with all dates since ANALYSIS_START_DATE
    # this is explicitly required to ensure res holds all dates, incl. non-trading dates
    # which are excluded by default by yfinance API 
    date_range = pd.date_range(
        start=ANALYSIS_START_DATE, 
        end=dt.now().date(),
        freq="D")

    res = pd.DataFrame(date_range, columns=["Date"])

    # get STOCK and MARKET ticker values for all analysis dates
    tickers = list(set(trade["ticker"] for trade in trades)) + [c.MARKET]
    data = yf.download(tickers, start=ANALYSIS_START_DATE, end=dt.now().date() + td(days=1), interval="1d", session=session)["Close"]
    res = res.merge(data, on="Date", how="left")

    # reset index to make Date a column
    res.reset_index(inplace=True)
    
    # forward fill values for dates where price data was not available
    res = res.ffill()

    # group trades by date 
    # then add a trades column containing list of trades on a given date
    trades_map = generate_trades_map(trades)
    res["trades"] = res["Date"].dt.date.map(lambda d: trades_map.get(d, []))

    # creates a shares column containing a dict of shares in portfolio upto that date
    # the size of dict increases as more trades are made
    # also creates a market_shares column containing number of market shares upto that date
    # this too increases as more trades are made
    res = calculate_cumulative_shares(res)

    # sum of portfolio value for all trades on a given date
    res[c.STOCK_PORTFOLIO_COL_NAME] = res.apply(calculate_portfolio_value, axis=1)

    # value of market shares on a given date
    res[c.MARKET_PORTFOLIO_COL_NAME] = res["market_shares"] * res[c.MARKET]

    # RAM reduction
    del data

    return res

def color_vals(val):
    """pd styler to color cell text based on value"""
    color = "green" if val > 0 else "red"
    return f"color: {color}"

def calculate_cumulative_shares(df):
    portfolio = {}  # Running portfolio dict
    cumulative_shares = []
    market_shares = []

    # assume starting with $0 in market
    # this increases as trades are made
    market_shares_bought = 0
    for _, row in df.iterrows():
        # Start with a copy of the previous day's portfolio
        current_portfolio = portfolio.copy()

        for trade in row.get('trades', []):
            ticker = trade['ticker']
            amount = trade['amount']

            ticker_price = row.get(ticker)
            market_price = row.get(c.MARKET)

            if ticker_price and ticker_price > 0 and market_price and market_price > 0:
                shares_bought = amount / ticker_price
                current_portfolio[ticker] = current_portfolio.get(ticker, 0) + shares_bought

                market_shares_bought += amount / market_price
        
        cumulative_shares.append(current_portfolio)
        market_shares.append(market_shares_bought)
        
        portfolio = current_portfolio  # Update for next iteration

    df['shares'] = cumulative_shares
    df['market_shares'] = market_shares

    return df

def calculate_portfolio_value(row):
    sum = 0
    for ticker, qty in row.get('shares', {}).items():
        sum += qty * row.get(ticker, 0)
            
    return sum

def generate_trades_map(trades):
    trades_map = {}
    for trade in trades:
        date = dt.strptime(trade["date"], c.DATES_FORMAT).date()
        if date not in trades_map:
            trades_map[date] = []
        trades_map[date].append(trade)

    return trades_map

def plot_results(res):
    plt.figure(figsize=(12, 6))
    plt.plot(res['Date'], res["portfolio_value"], label=c.STOCK_PORTFOLIO_LABEL)
    plt.plot(res['Date'], res["market_value"], label=c.MARKET_PORTFOLIO_LABEL)

    # Add annotations for trades
    for i, row in res.iterrows():
        if row.get("trades"):
            notes = ", ".join([f"{t['ticker']}" for t in row["trades"]])
            y_pos = row["portfolio_value"]
            plt.annotate(
                notes,
                xy=(row["Date"], y_pos),
                xytext=(0, 10),
                textcoords="offset points",
                fontsize=8,
                arrowprops=dict(arrowstyle="->", color="gray"),
                ha='center'
            )


    # axis formatting
    ax = plt.gca()
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d, %Y'))
    ax.yaxis.set_major_formatter(mticker.StrMethodFormatter('${x:,.0f}'))  # Currency format
    plt.xticks(rotation=45)

    # Formatting
    plt.ylabel('Portfolio Value ($)')
    plt.legend()
    plt.grid(True)

    return plt

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

            trade_id = f"{trade["date"]} | {trade["ticker"]}"
            purchase_price = row[trade["ticker"]]
            latest_price = latest_date[trade["ticker"]]
            latest_market_price = latest_date[c.MARKET]
            trades_summary.append({
                "ticker": trade["ticker"],
                "date": trade["date"],
                "purchase_price": purchase_price,
                "latest_price": latest_price,
                "return": (latest_price - purchase_price)/purchase_price,
                "market_return": (latest_market_price - market_purchase_price)/market_purchase_price
            })

            if purchase_price < latest_price:
                winners.append(trade_id)
            else:
                losers.append(trade_id)

    # metrics: number of winning/losing trades
    metrics.append({
        "label": "Winning Trades", 
        "value": len(winners),
        "help": "**Winning trades**\n\n" + "\n\n".join(winners) if winners else "No winners! 😞",
    })

    metrics.append({
        "label": "Losing Trades", 
        "value": len(losers),
        "help": "**Losing trades**\n\n" + "\n\n".join(losers) if losers else "No losers! 🎉",
    })

    # metric: success rate
    total_trades = res["trades"].apply(len).sum()
    winning_percentage = len(winners) / total_trades * 100 if total_trades else 0
    metrics.append({"label": "Success Rate", "value": f"{winning_percentage:.0f}%"})

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

    # allow tags to be empty
    # remaining cols must be filled
    if edited_trades[[col for col in edited_trades.columns if col != "tags"]].isnull().values.any():
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

def humanize_date(date_str):
    """
        accepts dates in YYYY-MM-DD format and returns a humanized version
        example: 2025-09-18 --> Thursday, September 18, 2025
    """
    return dt.strptime(date_str, c.DATES_FORMAT).strftime(c.PREFERRED_UI_DATE_FORMAT_DATETIME)

