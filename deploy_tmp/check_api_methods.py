import os, inspect
os.environ["APP_ENV"] = "prod"
from ganah import setup_api
api = setup_api("SHOONYA", "FA138862")
methods = [m for m in dir(api) if not m.startswith("_") and callable(getattr(api, m))]
for m in sorted(methods):
    if any(x in m.lower() for x in ["quote", "bid", "ask", "search", "option", "get_sec", "get_qu"]):
        print(m)
