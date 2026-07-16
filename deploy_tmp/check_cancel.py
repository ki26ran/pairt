import os
os.environ["APP_ENV"] = "prod"
from ganah import setup_api
api = setup_api("SHOONYA", "FA138862")
for m in sorted(dir(api)):
    if "cancel" in m.lower() or "exit" in m.lower():
        print(m)
