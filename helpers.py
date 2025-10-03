import io
import json
import config as c 
import pandas as pd
import yfinance as yf
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from curl_cffi import requests
from datetime import datetime as dt
from datetime import timedelta as td

# session is required to avoid 429s from yfinance
# I believe yfinance rate limits based on User-Agent header
# which, without this session, is set to python-requests
session = requests.Session(impersonate="chrome")

def load_trades(selected_tags=None):
    """
        Load trades from trades.json object in S3.

        If selected_tags is provided, only return matching trades
        else, return all 
    """

    response = c.s3.get_object(Bucket=c.S3_BUCKET, Key=c.TRADES_JSON_PATH)
    trades_str = response['Body'].read().decode('utf-8')
    trades = json.loads(trades_str)

    
    if selected_tags:
        return [
            trade for trade in trades
            if trade.get("tags") and any(tag in trade["tags"] for tag in selected_tags)
        ]
    else:
        return trades

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

    # Formatting
    plt.xlabel('Date')
    plt.ylabel('Portfolio Value ($)')
    plt.legend()
    plt.grid(True)

    # Format x-axis dates
    ax = plt.gca()
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d, %Y'))
    plt.xticks(rotation=45)

    return plt

def get_metrics(res):
    metrics = []

    # metric: number of trades
    metrics.append({
        "label": "Total Trades", 
        "value": res["trades"].apply(len).sum(),
        "help": "All trades are included except when filtered by tags."
    })

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

    # metric: winning percentage
    # winning_percentage = (number_of_winners / len(tickers) * 100) if tickers else 0
    # metrics.append({"label": "winning percentage", "value": f"{winning_percentage:.2f}%"})

    # metric: total invested
    metrics.append({"label": "Total Invested", "value": f"${total_invested:,.2f}"})

    # metrics: final portfolio values
    final_stock_value = res[c.STOCK_PORTFOLIO_COL_NAME].iloc[-1]
    final_market_value = res[c.MARKET_PORTFOLIO_COL_NAME].iloc[-1]

    sign = "" if final_stock_value - total_invested >= 0 else "-"
    delta = final_stock_value - total_invested
    delta_pct = (delta / total_invested * 100) if total_invested != 0 else 0
    metrics.append({
        "label": f"{c.STOCK_PORTFOLIO_LABEL} Value",
        "value": f"${final_stock_value:,.2f}",
        "delta": f"{sign}${abs(delta):,.2f} | {sign}{abs(delta_pct):.2f}%"
    })

    sign = "" if final_market_value - total_invested >= 0 else "-"
    delta = final_market_value - total_invested
    delta_pct = (delta / total_invested * 100) if total_invested != 0 else 0
    metrics.append({
        "label": f"{c.MARKET_PORTFOLIO_LABEL} Value", 
        "value": f"${final_market_value:,.2f}",
        "delta": f"{sign}${abs(delta):,.2f} | {sign}{abs(delta_pct):.2f}%"
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

