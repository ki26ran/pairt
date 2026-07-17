import json
cfg = {"live": False, "broker_name": "SHOONYA", "broker_username": "FA138862"}
with open("/opt/pairt/pairtrading/configs/broker_config.json", "w") as f:
    json.dump(cfg, f, indent=2)
print("Done:", json.dumps(cfg))
