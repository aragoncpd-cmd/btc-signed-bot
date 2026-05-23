from flask import Flask, request, jsonify
import requests, os, json
from datetime import datetime
import pandas as pd
import numpy as np

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")

# Mapeo de temporalidades a formato OKX
TIMEFRAMES = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "4h": "4H", "1d": "1D"}
UMBRAL_CONFIANZA = 70

OKX_SYMBOL = "BTC-USDT-SWAP"


def ema(series, length):
    return series.ewm(span=length, adjust=False).mean()


def rsi(series, length=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd_calc(series, fast=12, slow=26, signal=9):
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line


def get_okx_klines(interval, limit=200):
    url = f"https://www.okx.com/api/v5/market/candles?instId={OKX_SYMBOL}&bar={interval}&limit={limit}"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        if data.get("code") != "0":
            return None
        klines = data["data"]
        # OKX devuelve las velas en orden inverso (más reciente primero)
        klines.reverse()
        # OKX columns: [ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]
        df = pd.DataFrame(klines, columns=[
            "open_time", "open", "high", "low", "close", "volume", "volCcy", "volCcyQuote", "confirm"
        ])
        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["volume"] = df["volume"].astype(float)
        return df
    except Exception as e:
        return None


def calculate_indicators(df):
    if df is None or len(df) < 50:
        return None

    df["rsi"] = rsi(df["close"], length=14)
    df["ema_20"] = ema(df["close"], length=20)
    df["ema_50"] = ema(df["close"], length=50)
    df["ema_200"] = ema(df["close"], length=200)
    df["macd"], df["macd_signal"] = macd_calc(df["close"])

    last = df.iloc[-1]
    prev = df.iloc[-2]

    if last["close"] > last["ema_20"] > last["ema_50"]:
        ema_trend = "alcista_fuerte"
    elif last["close"] > last["ema_50"]:
        ema_trend = "alcista"
    elif last["close"] < last["ema_20"] < last["ema_50"]:
        ema_trend = "bajista_fuerte"
    elif last["close"] < last["ema_50"]:
        ema_trend = "bajista"
    else:
        ema_trend = "lateral"

    if pd.notna(last["macd"]) and pd.notna(last["macd_signal"]):
        if last["macd"] > last["macd_signal"] and prev["macd"] <= prev["macd_signal"]:
            macd_state = "cruce_alcista_reciente"
        elif last["macd"] < last["macd_signal"] and prev["macd"] >= prev["macd_signal"]:
            macd_state = "cruce_bajista_reciente"
        elif last["macd"] > last["macd_signal"]:
            macd_state = "alcista"
        else:
            macd_state = "bajista"
    else:
        macd_state = "n/d"

    avg_volume = df["volume"].tail(20).mean()
    vol_relative = last["volume"] / avg_volume if avg_volume > 0 else 1

    return {
        "precio": round(last["close"], 2),
        "rsi": round(last["rsi"], 2) if pd.notna(last["rsi"]) else None,
        "ema_20": round(last["ema_20"], 2) if pd.notna(last["ema_20"]) else None,
        "ema_50": round(last["ema_50"], 2) if pd.notna(last["ema_50"]) else None,
        "ema_200": round(last["ema_200"], 2) if pd.notna(last["ema_200"]) else None,
        "ema_trend": ema_trend,
        "macd_state": macd_state,
        "volumen_relativo": round(vol_relative, 2)
    }


def get_okx_funding():
    try:
        r1 = requests.get(f"https://www.okx.com/api/v5/public/funding-rate?instId={OKX_SYMBOL}", timeout=10)
        d1 = r1.json()
        r2 = requests.get(f"https://www.okx.com/api/v5/market/ticker?instId={OKX_SYMBOL}", timeout=10)
        d2 = r2.json()
        r3 = requests.get(f"https://www.okx.com/api/v5/public/open-interest?instId={OKX_SYMBOL}", timeout=10)
        d3 = r3.json()
        if d1.get("code") != "0" or d2.get("code") != "0":
            return {"error": "API OKX error"}
        funding = d1["data"][0]
        ticker = d2["data"][0]
        oi = d3["data"][0] if d3.get("code") == "0" and d3.get("data") else {}
        last_price = float(ticker["last"])
        open_24h = float(ticker["open24h"])
        price_change_pct = ((last_price - open_24h) / open_24h * 100) if open_24h > 0 else 0
        return {
            "mark_price": last_price,
            "funding_rate": float(funding["fundingRate"]) * 100,
            "open_interest": float(oi.get("oi", 0)),
            "volume_24h": float(ticker["vol24h"]),
            "price_change_24h_pct": round(price_change_pct, 2)
        }
    except Exception as e:
        return {"error": str(e)}

        funding = d1["data"][0]
        ticker = d2["data"][0]
        oi = d3["data"][0] if d3.get("code") == "0" and d3.get("data") else {}

        last_price = float(ticker["last"])
        open_24h = float(ticker["open24h"])
