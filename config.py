import os
import boto3
import streamlit as st

from datetime import date, timedelta
from dotenv import load_dotenv, find_dotenv
from pushover import Pushover

# load environment variables from .env file
load_dotenv(find_dotenv())
def env(key, default=None):
    var = os.getenv(key, default)
    if var is None:
        raise RuntimeError(f"Missing env var: {key}")
    return var

# app styling
PRIMARY_COLOR = "#4CAF50"

# user preferences
MARKET = "VTI"
DESIRED_TICKER_ATTRIBUTE = "Close"
NUM_DAYS_PRECEDING_ANALYSIS = 30

# app logic constants
DATES_FORMAT = "%Y-%m-%d"
STOCK_PORTFOLIO_COL_NAME = "portfolio_value"
MARKET_PORTFOLIO_COL_NAME = "market_value"
RES_CSV_PATH = "res.csv"

# date ~6 months prior to today, used to seed example trades for new users
_default_trade_date = (date.today() - timedelta(days=180)).strftime(DATES_FORMAT)

# new users are seeded with these example trades so the app loads populated.
# built DRY: trades share everything except ticker, source, and tag.
DEFAULT_TRADES = [
    {
        "ticker": ticker,
        "date": _default_trade_date,
        "amount": 1000.0,
        "notes": "example trade",
        "source": [source],
        "tags": [tag],
    }
    for ticker, source, tag in [
        ("AMZN", "Warren Buffett", "executed"),
        ("NVDA", "/r/wallstreetbets", "hypothetical"),
    ]
]

# columns a trade record carries; used to build an empty trades frame
# with the correct schema when a user has no trades yet
TRADES_COLUMNS = ["ticker", "date", "amount", "notes", "source", "tags"]

# UI vars
ASSETS_PATH = "assets"
PREFERRED_UI_DATE_FORMAT_MOMENTJS = "dddd, MMMM DD, YYYY"
PREFERRED_UI_DATE_FORMAT_DATETIME = "%A, %B %d, %Y"
STOCK_PORTFOLIO_LABEL = 'Stock Picking Portfolio'
MARKET_PORTFOLIO_LABEL = f'100% {MARKET} Portfolio'
COLUMN_CONFIGS = {
    "_index": None,
    "ticker": st.column_config.TextColumn("Ticker", width="small"),
    "date": st.column_config.DateColumn("Date", format=PREFERRED_UI_DATE_FORMAT_MOMENTJS),
    "purchase_price": st.column_config.NumberColumn("Purchase Price", format="dollar"),
    "latest_price": st.column_config.NumberColumn("Latest Price", format="dollar"),
    "amount": st.column_config.NumberColumn("Amount", format="dollar", width="small"),
    "notes": "Notes",
    "source": st.column_config.ListColumn("Source", width="small"),
    "return": st.column_config.NumberColumn("Trade Return", format="percent"),
    "market_return": st.column_config.NumberColumn("Market Return", format="percent"),
    "tags": st.column_config.ListColumn("Tags", width="medium")
}

# aws vars
S3_BUCKET = "pickwise-676206945006"
TRADES_JSON_FILENAME = "trades.json"
TICKER_DATA_FILENAME = "ticker_data.parquet"
AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY")
AWS_REGION = env("AWS_REGION")
s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)

ddb = boto3.resource(
    "dynamodb",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)

# ddb table names
USERS_TABLE = "users-pickwise"

# Google OAuth2Component instance
CLIENT_ID = env("GOOGLE_CLIENT_ID")
CLIENT_SECRET = env("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = env("REDIRECT_URI")
AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"

# misc auth/UI vars
LOGOUT_BUTTON_KEY_NAME = "logout_button"

# Pushover client
po = Pushover(user_token=env("PUSHOVER_USER_TOKEN"), app_token=env("PUSHOVER_APP_TOKEN"), log_token=env("PUSHOVER_LOG_TOKEN"))