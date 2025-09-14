import os
import boto3
import streamlit as st

from dotenv import load_dotenv, find_dotenv

# load environment variables from .env file
load_dotenv(find_dotenv())
def env(key, default=None):
    var = os.getenv(key, default)
    if var is None:
        raise RuntimeError(f"Missing env var: {key}")
    return var

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

# aws vars
S3_BUCKET = "pickwise-676206945006"
TRADES_JSON_PATH = "trades.json"
AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY")
AWS_REGION = env("AWS_REGION")
s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)