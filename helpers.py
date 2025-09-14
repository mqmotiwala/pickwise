import os
import json
import shutil
import config as c 
import yfinance as yf
import matplotlib.pyplot as plt

from curl_cffi import requests
from datetime import datetime as dt
from datetime import timedelta as td

# session is required to avoid 429s from yfinance
# I believe yfinance rate limits based on User-Agent header
# which, without this session, is set to python-requests
session = requests.Session(impersonate="chrome")

def load_trades(enabled_only=True):
    """
        Load trades from trades.json file.
        If enabled_only is True, only return trades with "enabled" key = true.
    """

    if not os.path.exists(c.TRADES_JSON_PATH):
        return c.DEFAULT_TRADES
        
    with open(c.TRADES_JSON_PATH, "r") as f:
        trades = json.load(f)

    if enabled_only:
        return [trade for trade in trades if trade.get("enabled", True)]
    else:
        return trades
    
def save_trades(edited_trades):
    # Create backup directory if it doesn't exist
    os.makedirs("backup", exist_ok=True)

    # Generate timestamped backup filename
    trades = load_trades(enabled_only=False)
    timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"backup/trades_backup_{timestamp}_cnt{len(trades)}.json"

    # Copy current trades.json to backup
    shutil.copy(c.TRADES_JSON_PATH, backup_path)

    # Save new trades.json
    with open(c.TRADES_JSON_PATH, "w") as f:
        edited_trades.to_json(f, orient="records", indent=4)

def generate_results():
    trades = load_trades()

    # get analysis start date based on earliest trade date
    ANALYSIS_START_DATE = min(
            dt.strptime(trade["date"], c.DATES_FORMAT) for trade in trades
        )- td(days=c.NUM_DAYS_PRECEDING_ANALYSIS)

    # get STOCK and MARKET ticker values for all analysis dates
    tickers = list(set(trade["ticker"] for trade in trades)) + [c.MARKET]
    res = yf.download(tickers, start=ANALYSIS_START_DATE, end=dt.now().date() + td(days=1), interval="1d", session=session)["Close"]

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

    # Formatting
    plt.xlabel('Date')
    plt.ylabel('Portfolio Value ($)')
    plt.legend()
    plt.grid(True)
            
    return plt

def get_metrics(res):
    metrics = []

    # metric: number of trades
    metrics.append({
        "label": "Total Trades", 
        "value": res["trades"].apply(len).sum(),
        "help": "Only enabled trades are included"
    })

    # metrics: number of winning/losing trades
    trades = load_trades()
    tickers = list(set(trade["ticker"] for trade in trades))
    
    number_of_winners = 0
    winners = []
    for ticker in tickers:
        # get first index with value for ticker
        first_valid = res[ticker].first_valid_index()
        first_value = res.loc[first_valid, ticker] if first_valid is not None else None
        last_value = res[ticker].iloc[-1]
        if first_value and last_value:
            if last_value > first_value:
                number_of_winners += 1
                winners.append(ticker)
        
    metrics.append({
        "label": "Winning Trades", 
        "value": number_of_winners,
        "help": f"Winning tickers: {', '.join(winners)}" if winners else "No winners! 😞"
    })

    metrics.append({
        "label": "Losing Trades", 
        "value": len(tickers) - number_of_winners,
        "help": f"Losing tickers: {', '.join([t for t in tickers if t not in winners])}" if len(tickers) - number_of_winners > 0 else "No losers! 🎉"
    })

    # metric: winning percentage
    # winning_percentage = (number_of_winners / len(tickers) * 100) if tickers else 0
    # metrics.append({"label": "winning percentage", "value": f"{winning_percentage:.2f}%"})

    # metric: total invested
    total_invested = sum(trade["amount"] for trade in trades)
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
    
    return metrics

def validate_changes(edited_trades):
    """
        runs a series of validations
        returns True if any validation fails alongside an error message
        else returns False, None
    """

    if edited_trades.isnull().values.any():
        return True, "You have trades with unfinished details."
    if edited_trades["date"].apply(lambda d: not isinstance(d, str) or len(d) != 10 or dt.strptime(d, c.DATES_FORMAT, ).strftime(c.DATES_FORMAT) != d).any():
        return True, "One or more dates are not in the correct format (YYYY-MM-DD)."
    if (edited_trades["amount"].apply(lambda a: not isinstance(a, (int, float)) or a <= 0)).any():
        return True, "One or more amounts are not valid positive numbers."

    return False, None