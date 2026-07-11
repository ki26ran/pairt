"""
PairTrading Telegram config — per-machine, not in git.
Stored at configs/telegram_config.json (gitignored).
Provides get/send functions for PairTrading alert bot.
"""
import os, json, socket

CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "configs", "telegram_config.json")
HOST_TAG = "[" + socket.gethostname().replace("LAPTOP-", "WIN-")[:12] + "]"


def get_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"bot_token": "", "chat_id": ""}


def update_config(bot_token, chat_id):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump({"bot_token": bot_token.strip(), "chat_id": chat_id.strip()}, f, indent=2)


def is_configured():
    c = get_config()
    return bool(c.get("bot_token") and c.get("chat_id"))


def send_message(text, parse_mode="Markdown"):
    cfg = get_config()
    if not cfg.get("bot_token") or not cfg.get("chat_id"):
        return False
    try:
        import requests
        url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
        r = requests.post(url, json={
            "chat_id": cfg["chat_id"],
            "text": text + " " + HOST_TAG,
            "parse_mode": parse_mode
        }, timeout=10)
        return r.status_code == 200
    except Exception:
        return False
