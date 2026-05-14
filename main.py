from flask import Flask, request, jsonify
import requests, os, json
from datetime import datetime

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
COINGLASS_KEY = os.environ.get("COINGLASS_KEY")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")

def get_coinglass_data():
    headers = {"CG-API-KEY": COINGLASS_KEY}
    data = {}
    try:
        oi_r = requests.get("https://open-api-v4.coinglass.com/api/futures/open-interest/aggregated-history?symbol=BTC&interval=4h&limit=10", headers=headers, timeout=10)
        data["oi"] = oi_r.json()
    except Exception as e:
        data["oi_error"] = str(e)
    try:
        f_r = requests.get("https://open-api-v4.coinglass.com/api/futures/funding-rate/history?symbol=BTC&exchange=Binance&interval=4h&limit=10", headers=headers, timeout=10)
        data["funding"] = f_r.json()
    except Exception as e:
        data["funding_error"] = str(e)
    return data

def analyze_with_claude(tv_data, cg_data):
    prompt = f"""Analizá este estado del mercado BTC/USDT Futuros y respondé SOLO en JSON válido sin texto adicional.

DATOS TRADINGVIEW:
{json.dumps(tv_data, indent=2, ensure_ascii=False)}

DATOS COINGLASS:
{json.dumps(cg_data, indent=2, ensure_ascii=False)[:3000]}

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
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 500,
            "messages": [{"role": "user", "content": prompt}]
        },
        timeout=30
    )
    
    response_data = response.json()
    
    # Si la respuesta no tiene "content", devolvemos el error de Anthropic
    if "content" not in response_data:
        return {
            "señal": "NEUTRAL",
            "confianza": 0,
            "entrada": "N/A",
            "tp1": "N/A",
            "tp2": "N/A",
            "sl": "N/A",
            "apalancamiento": 1,
            "razon": f"Error de Anthropic: {json.dumps(response_data)[:300]}"
        }
    
    text = response_data["content"][0]["text"]
    try:
        return json.loads(text.replace("```json","").replace("```","").strip())
    except Exception as e:
        return {
            "señal": "NEUTRAL",
            "confianza": 0,
            "entrada": "N/A",
            "tp1": "N/A",
            "tp2": "N/A",
            "sl": "N/A",
            "apalancamiento": 1,
            "razon": f"No se pudo parsear respuesta IA: {text[:200]}"
        }

def send_telegram(signal):
    if signal["señal"] == "NEUTRAL" or signal["confianza"] < 65:
        return {"sent": False, "reason": "Señal NEUTRAL o confianza baja"}
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

    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
        timeout=10
    )
    return {"sent": True, "telegram_response": r.json()}

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        tv_data = request.json or {}
        cg_data = get_coinglass_data()
        signal = analyze_with_claude(tv_data, cg_data)
        tg_result = send_telegram(signal)
        return jsonify({
            "status": "ok",
            "signal": signal,
            "telegram": tg_result
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/test-telegram")
def test_telegram():
    """Endpoint para probar solo Telegram sin IA ni Coinglass"""
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

@app.route("/")
def health():
    return jsonify({"status": "BTC Signal Bot corriendo", "time": str(datetime.now())})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
