import os, json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Broker config — loaded from broker_config.json, falls back to hardcoded defaults
BROKER_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "broker_config.json")

LIVE = False
BROKER_NAME = "xyz"
BROKER_USERNAME = "pqr"

if os.path.exists(BROKER_CONFIG_FILE):
    try:
        with open(BROKER_CONFIG_FILE) as f:
            cfg = json.load(f)
        LIVE = cfg.get("live", LIVE)
        BROKER_NAME = cfg.get("broker_name", BROKER_NAME)
        BROKER_USERNAME = cfg.get("broker_username", BROKER_USERNAME)
    except Exception as e:
        print(f"[WARN] Failed to load broker_config.json: {e}")
