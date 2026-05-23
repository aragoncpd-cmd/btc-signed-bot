from flask import Flask, request, jsonify
import requests, os, json
from datetime import datetime
import pandas as pd
import pandas_ta as ta

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")

# Mapeo de temporalidades a formato Bybit
TIMEFRAMES = {
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "4h": "240",
    "1d": "D"
}

UMBRAL_CONFIANZA = 70


def get_bybit_klines(interval, limit=200):
    """Pide velas de Bybit para BTCUSDT perpetual"""
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval={interval}&limit={limit}"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        if data.get("retCode") != 0:
            return None
        klines = data["result"]["list"]
        # Bybit devuelve las velas en orden inverso (más reciente primero)
        klines.reverse()
        df = pd.DataFrame(klines, columns=[
            "open_time", "open", "high", "low", "close", "volume", "turnover"
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

    df["rsi"] = ta.rsi(df["close"], length=14)
    df["ema_20"] = ta.ema(df["close"], length=20)
    df["ema_50"] = ta.ema(df["close"], length=50)
    df["ema_200"] = ta.ema(df["close"], length=200)
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd is not None:
        df["macd"] = macd["MACD_12_26_9"]
        df["macd_signal"] = macd["MACDs_12_26_9"]

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

    if pd.notna(last.get("macd")) and pd.notna(last.get("macd_signal")):
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


def get_bybit_funding():
    """Pide funding rate de Bybit Futures"""
    try:
        r = requests.get("https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT", timeout=10)
        data = r.json()
        if data.get("retCode") != 0:
            return {"error": "API Bybit error"}
        ticker = data["result"]["list"][0]
        return {
            "mark_price": float(ticker["markPrice"]),
            "funding_rate": float(ticker["fundingRate"]) * 100,
            "open_interest": float(ticker["openInterest"]),
            "volume_24h": float(ticker["volume24h"]),
            "price_change_24h_pct": float(ticker["price24hPcnt"]) * 100
        }
    except Exception as e:
        return {"error": str(e)}


def collect_all_data():
    market_data = {"temporalidades": {}}
    for name, interval in TIMEFRAMES.items():
        df = get_bybit_klines(interval)
        indicators = calculate_indicators(df)
        if indicators:
            market_data["temporalidades"][name] = indicators
    market_data["futures_data"] = get_bybit_funding()
    market_data["timestamp"] = str(datetime.now())
    return market_data


def analyze_with_claude(market_data, contexto_extra=""):
    prompt = f"""Sos un analista experto en trading de futuros de Bitcoin (BTC/USDT).

Analizá los siguientes datos REALES del mercado y decidí si hay una señal clara de LONG o SHORT.

DATOS DEL MERCADO (Bybit):
{json.dumps(market_data, indent=2, ensure_ascii=False)}

{f'CONTEXTO ADICIONAL: {contexto_extra}' if contexto_extra else ''}

LONG cuando hay confluencia de:
- RSI < 35 en al menos 2 temporalidades
- Tendencia EMA alcista en 4h o 1d
- MACD cruce alcista o estado alcista
- Funding negativo o cercano a 0
- Volumen relativo > 1

SHORT cuando hay confluencia de:
- RSI > 65 en al menos 2 temporalidades
- Tendencia EMA bajista en 4h o 1d
- MACD cruce bajista o estado bajista
- Funding muy positivo (> 0.05%)
- Volumen relativo > 1

NEUTRAL cuando no hay confluencia clara.

Respondé SOLO con este JSON exacto:
{{
  "señal": "LONG" o "SHORT" o "NEUTRAL",
  "confianza": número 1-100,
  "entrada": "precio",
  "tp1": "precio",
  "tp2": "precio",
  "sl": "precio",
  "apalancamiento": número entre 2 y 10,
  "razon": "explicación breve de la confluencia detectada"
}}"""

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5",
                "max_tokens": 800,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )
        data = response.json()
        if "content" not in data:
            return {"señal": "NEUTRAL", "confianza": 0, "razon": f"Error Anthropic: {json.dumps(data)[:200]}"}
        text = data["content"][0]["text"]
        return json.loads(text.replace("```json", "").replace("```", "").strip())
    except Exception as e:
        return {"señal": "NEUTRAL", "confianza": 0, "razon": f"Error: {str(e)}"}


def send_telegram(signal, contexto=""):
    if signal.get("señal") == "NEUTRAL" or signal.get("confianza", 0) < UMBRAL_CONFIANZA:
        return {"sent": False, "reason": f"Señal {signal.get('señal')} con confianza {signal.get('confianza')}%"}
    emoji = "🟢" if signal["señal"] == "LONG" else "🔴"
    msg = f"""{emoji} *SEÑAL BTC/USDT — {signal['señal']}*

💰 Entrada: `{signal.get('entrada', 'N/A')}`
🎯 TP1: `{signal.get('tp1', 'N/A')}`
🎯 TP2: `{signal.get('tp2', 'N/A')}`
🛑 SL: `{signal.get('sl', 'N/A')}`
⚡ Apalancamiento: `{signal.get('apalancamiento', 'N/A')}x`
📊 Confianza: `{signal.get('confianza')}%`

📝 {signal.get('razon', '')}
{f'{chr(10)}🔔 Trigger: {contexto}' if contexto else ''}

⚠️ _Gestioná siempre tu riesgo._"""
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
        return {"sent": True, "telegram": r.json()}
    except Exception as e:
        return {"sent": False, "error": str(e)}


def run_analysis(contexto_extra=""):
    market_data = collect_all_data()
    signal = analyze_with_claude(market_data, contexto_extra)
    tg_result = send_telegram(signal, contexto_extra)
    return {"market_data": market_data, "signal": signal, "telegram": tg_result}


@app.route("/")
def health():
    return jsonify({"status": "BTC Signal Bot v6 (Bybit) corriendo", "time": str(datetime.now())})


@app.route("/analyze")
def analyze_endpoint():
    result = run_analysis()
    return jsonify(result)


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        tv_data = request.json or {}
        contexto = json.dumps(tv_data)
        result = run_analysis(contexto_extra=contexto)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/test-telegram")
def test_telegram():
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": "🧪 Test desde el servidor — Telegram funciona correctamente",
                "parse_mode": "Markdown"
            },
            timeout=10
        )
        return jsonify({"status": "ok", "telegram": r.json()})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/market-data")
def market_data_endpoint():
    """Ver los datos del mercado sin analizar (debug)"""
    data = collect_all_data()
    return jsonify(data)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
