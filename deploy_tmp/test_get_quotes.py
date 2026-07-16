import os, json
os.environ["APP_ENV"] = "prod"
from ganah import setup_api
api = setup_api("SHOONYA", "FA138862")

# Test get_quotes with the KFINTECH option symbol
try:
    q = api.get_quotes("NFO", "KFINTECH28JUL26P900")
    print("get_quotes with symbol:")
    print(json.dumps(q, indent=2, default=str))
except Exception as e:
    print(f"get_quotes(symbol) failed: {e}")

# Try searchscrip to get the token
try:
    s = api.searchscrip("NFO", "KFINTECH28JUL26P900")
    print("\nsearchscrip result:")
    print(json.dumps(s, indent=2, default=str) if s else "None")
except Exception as e:
    print(f"searchscrip failed: {e}")
