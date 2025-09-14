import streamlit as st

# app styling
st.set_page_config(layout="wide")

# user preferences
MARKET = "VTI"
DESIRED_TICKER_ATTRIBUTE = "Close"
NUM_DAYS_PRECEDING_ANALYSIS = 30

# app logic constants
DATES_FORMAT = "%Y-%m-%d"
STOCK_PORTFOLIO_COL_NAME = "portfolio_value"
MARKET_PORTFOLIO_COL_NAME = "market_value"
TRADES_JSON_PATH = "/data/db/trades.json"
DEFAULT_TRADES = [{
        "ticker":"AMZN",
        "date":"2025-04-01",
        "amount":1000.0,
        "notes":"sample trade",
        "enabled": True
    }]

# plot labels
STOCK_PORTFOLIO_LABEL = 'Stock Picking Portfolio'
MARKET_PORTFOLIO_LABEL = f'100% {MARKET} Portfolio'