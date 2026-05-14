from flask import Flask, request, jsonify
import requests, os, json
from datetime import datetime

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
COINGLASS_KEY = os.environ.get("COINGLASS_KEY")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")

def get_coinglass_data():
    headers = {"coinglassSecret": COINGLASS_KEY}
    try:
        oi = requests.get("https://open-api.coinglass.com/public/v2/open_interest?symbol=BTC&interval=h4", headers=headers, timeout=10).json()
        funding = requests.get("https://open-api.coinglass.com/public/v2/funding?symbol=BTC", headers=headers, timeout=10).json()
        return {"oi": oi, "funding": funding}
    except Exception as e:
        return {"error": str(e)}

def analyze_with_claude(tv_data, cg_data):
    prompt = f"""Analizá este estado del mercado BTC/USDT Futuros y respondé SOLO en JSON:

DATOS TRADINGVIEW:
{json.dumps(tv_data, indent=2)}

DATOS COINGLASS:
{json.dumps(cg_data, indent=2)}

Respondé con este JSON exacto:
{{
  "señal": "LONG" o "SHORT" o "NEUTRAL",
  "confianza": número 1-100,
  "entrada": "precio",
  "tp1": "precio",
  "tp2": "precio",
  "sl": "precio",
  "apalancamiento": número,
  "razon": "explicación breve"
}}"""

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": "claude-sonnet-4-20250514", "max_tokens": 500, "messages": [{"role": "user", "content": prompt}]},
        timeout=30
    )
    text = response.json()["content"][0]["text"]
    return json.loads(text.replace("```json","").replace("```","").strip())

def send_telegram(signal):
    if signal["señal"] == "NEUTRAL" or signal["confianza"] < 65:
        return
    emoji = "🟢" if signal["señal"] == "LONG" else "🔴"
    msg = f"""{emoji} *SEÑAL BTC/USDT — {signal['señal']}*

💰 Entrada: `{signal['entrada']}`
🎯 TP1: `{signal['tp1']}`
🎯 TP2: `{signal['tp2']}`
🛑 SL: `{signal['sl']}`
⚡ Apalancamiento: `{signal['apalancamiento']}x`
📊 Confianza: `{signal['confianza']}%`

📝 {signal['razon']}

⚠️ _Gestioná siempre tu riesgo._"""

    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
        timeout=10
    )

@app.route("/webhook", methods=["POST"])
def webhook():
    tv_data = request.json
    cg_data = get_coinglass_data()
    signal = analyze_with_claude(tv_data, cg_data)
    send_telegram(signal)
    return jsonify({"status": "ok", "signal": signal})

@app.route("/")
def health():
    return jsonify({"status": "BTC Signal Bot corriendo", "time": str(datetime.now())})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
