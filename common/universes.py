# Lot sizes for NSE F&O stocks (as of 2026)
import os, json
SYMBOLS_DATA = {
    "360ONE": {"lot": 500, "tick": 0.1},
    "ABB": {"lot": 125, "tick": 0.5},
    "ABCAPITAL": {"lot": 3100, "tick": 0.05},
    "ADANIENSOL": {"lot": 675, "tick": 0.1},
    "ADANIENT": {"lot": 309, "tick": 0.1},
    "ADANIGREEN": {"lot": 600, "tick": 0.1},
    "ADANIPORTS": {"lot": 475, "tick": 0.1},
    "ADANIPOWER": {"lot": 3550, "tick": 0.01},
    "ALKEM": {"lot": 125, "tick": 0.5},
    "AMBER": {"lot": 100, "tick": 0.5},
    "AMBUJACEM": {"lot": 1200, "tick": 0.05},
    "ANGELONE": {"lot": 2500, "tick": 0.05},
    "APLAPOLLO": {"lot": 350, "tick": 0.1},
    "APOLLOHOSP": {"lot": 125, "tick": 0.5},
    "ASHOKLEY": {"lot": 5000, "tick": 0.01},
    "ASIANPAINT": {"lot": 250, "tick": 0.1},
    "ASTRAL": {"lot": 425, "tick": 0.1},
    "AUBANK": {"lot": 1000, "tick": 0.05},
    "AUROPHARMA": {"lot": 550, "tick": 0.1},
    "AXISBANK": {"lot": 625, "tick": 0.1},
    "BAJAJ-AUTO": {"lot": 75, "tick": 1.0},
    "BAJAJFINSV": {"lot": 300, "tick": 0.1},
    "BAJAJHLDNG": {"lot": 75, "tick": 1.0},
    "BAJFINANCE": {"lot": 750, "tick": 0.05},
    "BANDHANBNK": {"lot": 3600, "tick": 0.01},
    "BANKBARODA": {"lot": 2925, "tick": 0.05},
    "BANKINDIA": {"lot": 5200, "tick": 0.01},
    "BDL": {"lot": 425, "tick": 0.1},
    "BEL": {"lot": 1425, "tick": 0.05},
    "BHARATFORG": {"lot": 500, "tick": 0.1},
    "BHARTIARTL": {"lot": 475, "tick": 0.1},
    "BHEL": {"lot": 2625, "tick": 0.05},
    "BIOCON": {"lot": 2500, "tick": 0.05},
    "BLUESTARCO": {"lot": 325, "tick": 0.1},
    "BOSCHLTD": {"lot": 25, "tick": 5.0},
    "BPCL": {"lot": 1975, "tick": 0.05},
    "BRITANNIA": {"lot": 125, "tick": 0.5},
    "BSE": {"lot": 375, "tick": 0.1},
    "CAMS": {"lot": 825, "tick": 0.05},
    "CANBK": {"lot": 6750, "tick": 0.01},
    "CDSL": {"lot": 475, "tick": 0.1},
    "CGPOWER": {"lot": 850, "tick": 0.05},
    "CHOLAFIN": {"lot": 625, "tick": 0.1},
    "CIPLA": {"lot": 425, "tick": 0.1},
    "COALINDIA": {"lot": 1350, "tick": 0.05},
    "COCHINSHIP": {"lot": 400, "tick": 0.1},
    "COFORGE": {"lot": 475, "tick": 0.1},
    "COLPAL": {"lot": 275, "tick": 0.1},
    "CONCOR": {"lot": 1250, "tick": 0.05},
    "CROMPTON": {"lot": 2150, "tick": 0.05},
    "CUMMINSIND": {"lot": 200, "tick": 0.5},
    "DABUR": {"lot": 1250, "tick": 0.05},
    "DALBHARAT": {"lot": 325, "tick": 0.1},
    "DELHIVERY": {"lot": 2075, "tick": 0.05},
    "DIVISLAB": {"lot": 100, "tick": 0.5},
    "DIXON": {"lot": 50, "tick": 1.0},
    "DLF": {"lot": 950, "tick": 0.05},
    "DMART": {"lot": 150, "tick": 0.1},
    "DRREDDY": {"lot": 625, "tick": 0.1},
    "EICHERMOT": {"lot": 100, "tick": 0.5},
    "ETERNAL": {"lot": 2425, "tick": 0.05},
    "EXIDEIND": {"lot": 1800, "tick": 0.05},
    "FEDERALBNK": {"lot": 2500, "tick": 0.05},
    "FORCEMOT": {"lot": 25, "tick": 1.0},
    "FORTIS": {"lot": 775, "tick": 0.05},
    "GAIL": {"lot": 3550, "tick": 0.01},
    "GLENMARK": {"lot": 375, "tick": 0.1},
    "GMRAIRPORT": {"lot": 6975, "tick": 0.01},
    "GODFRYPHLP": {"lot": 275, "tick": 0.1},
    "GODREJCP": {"lot": 500, "tick": 0.1},
    "GODREJPROP": {"lot": 325, "tick": 0.1},
    "GRASIM": {"lot": 250, "tick": 0.1},
    "GVT&D": {"lot": 125, "tick": 0.5},
    "HAL": {"lot": 150, "tick": 0.1},
    "HAVELLS": {"lot": 500, "tick": 0.1},
    "HCLTECH": {"lot": 400, "tick": 0.1},
    "HDFCAMC": {"lot": 300, "tick": 0.1},
    "HDFCBANK": {"lot": 650, "tick": 0.05},
    "HDFCLIFE": {"lot": 1100, "tick": 0.05},
    "HEROMOTOCO": {"lot": 150, "tick": 0.1},
    "HINDALCO": {"lot": 700, "tick": 0.1},
    "HINDPETRO": {"lot": 2025, "tick": 0.05},
    "HINDUNILVR": {"lot": 300, "tick": 0.1},
    "HINDZINC": {"lot": 1225, "tick": 0.05},
    "HYUNDAI": {"lot": 275, "tick": 0.1},
    "ICICIBANK": {"lot": 700, "tick": 0.1},
    "ICICIGI": {"lot": 325, "tick": 0.1},
    "ICICIPRULI": {"lot": 925, "tick": 0.05},
    "IDEA": {"lot": 71475, "tick": 0.01},
    "IDFCFIRSTB": {"lot": 9275, "tick": 0.01},
    "IEX": {"lot": 4350, "tick": 0.01},
    "INDHOTEL": {"lot": 1000, "tick": 0.05},
    "INDIANB": {"lot": 1000, "tick": 0.05},
    "INDIGO": {"lot": 150, "tick": 0.1},
    "INDUSINDBK": {"lot": 700, "tick": 0.05},
    "INDUSTOWER": {"lot": 1700, "tick": 0.05},
    "INFY": {"lot": 400, "tick": 0.1},
    "INOXWIND": {"lot": 6400, "tick": 0.01},
    "IOC": {"lot": 4875, "tick": 0.01},
    "IREDA": {"lot": 4525, "tick": 0.01},
    "IRFC": {"lot": 5425, "tick": 0.01},
    "ITC": {"lot": 1725, "tick": 0.05},
    "JINDALSTEL": {"lot": 625, "tick": 0.1},
    "JIOFIN": {"lot": 2350, "tick": 0.01},
    "JSWENERGY": {"lot": 1075, "tick": 0.05},
    "JSWSTEEL": {"lot": 675, "tick": 0.1},
    "JUBLFOOD": {"lot": 1250, "tick": 0.05},
    "KALYANKJIL": {"lot": 1350, "tick": 0.05},
    "KAYNES": {"lot": 150, "tick": 0.1},
    "KEI": {"lot": 175, "tick": 0.5},
    "KFINTECH": {"lot": 575, "tick": 0.05},
    "KOTAKBANK": {"lot": 2000, "tick": 0.05},
    "KPITTECH": {"lot": 775, "tick": 0.05},
    "LAURUSLABS": {"lot": 850, "tick": 0.1},
    "LICHSGFIN": {"lot": 1000, "tick": 0.05},
    "LICI": {"lot": 1400, "tick": 0.05},
    "LODHA": {"lot": 625, "tick": 0.05},
    "LT": {"lot": 175, "tick": 0.1},
    "LTF": {"lot": 2250, "tick": 0.05},
    "LTM": {"lot": 150, "tick": 0.1},
    "LUPIN": {"lot": 425, "tick": 0.1},
    "M&M": {"lot": 200, "tick": 0.1},
    "MANAPPURAM": {"lot": 3000, "tick": 0.05},
    "MANKIND": {"lot": 250, "tick": 0.1},
    "MARICO": {"lot": 1200, "tick": 0.05},
    "MARUTI": {"lot": 50, "tick": 1.0},
    "MAXHEALTH": {"lot": 525, "tick": 0.05},
    "MAZDOCK": {"lot": 225, "tick": 0.1},
    "MCX": {"lot": 625, "tick": 0.1},
    "MFSL": {"lot": 400, "tick": 0.1},
    "MOTHERSON": {"lot": 6150, "tick": 0.05},
    "MOTILALOFS": {"lot": 775, "tick": 0.05},
    "MPHASIS": {"lot": 275, "tick": 0.1},
    "MUTHOOTFIN": {"lot": 275, "tick": 0.1},
    "NAM-INDIA": {"lot": 625, "tick": 0.1},
    "NATIONALUM": {"lot": 1875, "tick": 0.05},
    "NAUKRI": {"lot": 550, "tick": 0.05},
    "NBCC": {"lot": 6500, "tick": 0.01},
    "NESTLEIND": {"lot": 500, "tick": 0.1},
    "NHPC": {"lot": 6950, "tick": 0.01},
    "NMDC": {"lot": 6750, "tick": 0.01},
    "NTPC": {"lot": 1500, "tick": 0.05},
    "NUVAMA": {"lot": 500, "tick": 0.1},
    "NYKAA": {"lot": 3125, "tick": 0.05},
    "OBEROIRLTY": {"lot": 350, "tick": 0.1},
    "OFSS": {"lot": 100, "tick": 0.5},
    "OIL": {"lot": 1400, "tick": 0.05},
    "ONGC": {"lot": 2250, "tick": 0.05},
    "PAGEIND": {"lot": 20, "tick": 5.0},
    "PATANJALI": {"lot": 1075, "tick": 0.05},
    "PAYTM": {"lot": 725, "tick": 0.1},
    "PERSISTENT": {"lot": 125, "tick": 0.5},
    "PETRONET": {"lot": 1900, "tick": 0.05},
    "PFC": {"lot": 1300, "tick": 0.05},
    "PGEL": {"lot": 950, "tick": 0.05},
    "PHOENIXLTD": {"lot": 350, "tick": 0.1},
    "PIDILITIND": {"lot": 500, "tick": 0.1},
    "PIIND": {"lot": 175, "tick": 0.1},
    "PNB": {"lot": 8000, "tick": 0.01},
    "PNBHOUSING": {"lot": 650, "tick": 0.1},
    "POLICYBZR": {"lot": 350, "tick": 0.1},
    "POLYCAB": {"lot": 125, "tick": 0.5},
    "POWERGRID": {"lot": 1900, "tick": 0.05},
    "POWERINDIA": {"lot": 25, "tick": 5.0},
    "PREMIERENE": {"lot": 650, "tick": 0.1},
    "PRESTIGE": {"lot": 450, "tick": 0.1},
    "RADICO": {"lot": 150, "tick": 0.1},
    "RBLBANK": {"lot": 3175, "tick": 0.05},
    "RECLTD": {"lot": 1575, "tick": 0.05},
    "RELIANCE": {"lot": 500, "tick": 0.1},
    "RVNL": {"lot": 1925, "tick": 0.01},
    "SAIL": {"lot": 4700, "tick": 0.01},
    "SAMMAANCAP": {"lot": 4300, "tick": 0.01},
    "SBICARD": {"lot": 800, "tick": 0.05},
    "SBILIFE": {"lot": 375, "tick": 0.1},
    "SBIN": {"lot": 750, "tick": 0.05},
    "SHREECEM": {"lot": 25, "tick": 5.0},
    "SHRIRAMFIN": {"lot": 825, "tick": 0.05},
    "SIEMENS": {"lot": 175, "tick": 0.1},
    "SOLARINDS": {"lot": 50, "tick": 1.0},
    "SONACOMS": {"lot": 1225, "tick": 0.05},
    "SRF": {"lot": 200, "tick": 0.1},
    "SUNPHARMA": {"lot": 350, "tick": 0.1},
    "SUPREMEIND": {"lot": 175, "tick": 0.1},
    "SUZLON": {"lot": 12700, "tick": 0.01},
    "SWIGGY": {"lot": 1825, "tick": 0.05},
    "TATACHEM": {"lot": 700, "tick": 0.1},
    "TATACONSUM": {"lot": 550, "tick": 0.1},
    "TATAELXSI": {"lot": 125, "tick": 0.1},
    "TATAPOWER": {"lot": 1450, "tick": 0.05},
    "TATASTEEL": {"lot": 2750, "tick": 0.01},
    "TCS": {"lot": 225, "tick": 0.1},
    "TECHM": {"lot": 600, "tick": 0.1},
    "TIINDIA": {"lot": 200, "tick": 0.1},
    "TITAN": {"lot": 175, "tick": 0.1},
    "TMPV": {"lot": 1600, "tick": 0.05},
    "TORNTPHARM": {"lot": 125, "tick": 0.1},
    "TRENT": {"lot": 225, "tick": 0.1},
    "TVSMOTOR": {"lot": 175, "tick": 0.1},
    "ULTRACEMCO": {"lot": 50, "tick": 1.0},
    "UNIONBANK": {"lot": 4425, "tick": 0.01},
    "UNITDSPR": {"lot": 400, "tick": 0.1},
    "UNOMINDA": {"lot": 550, "tick": 0.1},
    "UPL": {"lot": 1355, "tick": 0.05},
    "VBL": {"lot": 1275, "tick": 0.05},
    "VEDL": {"lot": 1150, "tick": 0.05},
    "VMM": {"lot": 4850, "tick": 0.01},
    "VOLTAS": {"lot": 375, "tick": 0.1},
    "WAAREEENER": {"lot": 175, "tick": 0.1},
    "WIPRO": {"lot": 3000, "tick": 0.01},
    "YESBANK": {"lot": 31100, "tick": 0.01},
    "ZYDUSLIFE": {"lot": 900, "tick": 0.1},
}

# ── Sector classification ───────────────────────────────────
SECTOR_MAP = {
    "360ONE": "Financial Services", "ABB": "Capital Goods",
    "ABCAPITAL": "Financial Services", "ADANIENSOL": "Energy & Power",
    "ADANIENT": "Diversified", "ADANIGREEN": "Energy & Power",
    "ADANIPORTS": "Infrastructure", "ADANIPOWER": "Energy & Power",
    "ALKEM": "Pharma & Healthcare", "AMBER": "Consumer Durables",
    "AMBUJACEM": "Cement", "ANGELONE": "Financial Services",
    "APLAPOLLO": "Metals", "APOLLOHOSP": "Pharma & Healthcare",
    "ASHOKLEY": "Automobile", "ASIANPAINT": "Consumer",
    "ASTRAL": "Chemicals", "AUBANK": "Banking",
    "AUROPHARMA": "Pharma & Healthcare", "AXISBANK": "Banking",
    "BAJAJ-AUTO": "Automobile", "BAJAJFINSV": "Financial Services",
    "BAJAJHLDNG": "Financial Services", "BAJFINANCE": "Financial Services",
    "BANDHANBNK": "Banking", "BANKBARODA": "Banking",
    "BANKINDIA": "Banking", "BDL": "Defence",
    "BEL": "Defence", "BHARATFORG": "Auto Ancillary",
    "BHARTIARTL": "Telecom", "BHEL": "Capital Goods",
    "BIOCON": "Pharma & Healthcare", "BLUESTARCO": "Consumer Durables",
    "BOSCHLTD": "Auto Ancillary", "BPCL": "Oil & Gas",
    "BRITANNIA": "FMCG", "BSE": "Financial Services",
    "CAMS": "Financial Services", "CANBK": "Banking",
    "CDSL": "Financial Services", "CGPOWER": "Capital Goods",
    "CHOLAFIN": "Financial Services", "CIPLA": "Pharma & Healthcare",
    "COALINDIA": "Metals", "COCHINSHIP": "Ship Building",
    "COFORGE": "IT & Technology", "COLPAL": "FMCG",
    "CONCOR": "Logistics", "CROMPTON": "Consumer Durables",
    "CUMMINSIND": "Capital Goods", "DABUR": "FMCG",
    "DALBHARAT": "Cement", "DELHIVERY": "Logistics",
    "DIVISLAB": "Pharma & Healthcare", "DIXON": "Consumer Durables",
    "DLF": "Real Estate", "DMART": "Retail",
    "DRREDDY": "Pharma & Healthcare", "EICHERMOT": "Automobile",
    "ETERNAL": "Pharma & Healthcare", "EXIDEIND": "Auto Ancillary",
    "FEDERALBNK": "Banking", "FORCEMOT": "Automobile",
    "FORTIS": "Pharma & Healthcare", "GAIL": "Oil & Gas",
    "GLENMARK": "Pharma & Healthcare", "GMRAIRPORT": "Infrastructure",
    "GODFRYPHLP": "FMCG", "GODREJCP": "FMCG",
    "GODREJPROP": "Real Estate", "GRASIM": "Cement",
    "GVT&D": "Capital Goods", "HAL": "Defence",
    "HAVELLS": "Consumer Durables", "HCLTECH": "IT & Technology",
    "HDFCAMC": "Financial Services", "HDFCBANK": "Banking",
    "HDFCLIFE": "Insurance", "HEROMOTOCO": "Automobile",
    "HINDALCO": "Metals", "HINDPETRO": "Oil & Gas",
    "HINDUNILVR": "FMCG", "HINDZINC": "Metals",
    "HYUNDAI": "Automobile", "ICICIBANK": "Banking",
    "ICICIGI": "Insurance", "ICICIPRULI": "Insurance",
    "IDEA": "Telecom", "IDFCFIRSTB": "Banking",
    "IEX": "Energy & Power", "INDHOTEL": "Hotels & Tourism",
    "INDIANB": "Banking", "INDIGO": "Aviation",
    "INDUSINDBK": "Banking", "INDUSTOWER": "Telecom",
    "INFY": "IT & Technology", "INOXWIND": "Energy & Power",
    "IOC": "Oil & Gas", "IREDA": "Financial Services",
    "IRFC": "Financial Services", "ITC": "FMCG",
    "JINDALSTEL": "Metals", "JIOFIN": "Financial Services",
    "JSWENERGY": "Energy & Power", "JSWSTEEL": "Metals",
    "JUBLFOOD": "FMCG", "KALYANKJIL": "Jewellery",
    "KAYNES": "Consumer Durables", "KEI": "Capital Goods",
    "KFINTECH": "Financial Services", "KOTAKBANK": "Banking",
    "KPITTECH": "IT & Technology", "LAURUSLABS": "Pharma & Healthcare",
    "LICHSGFIN": "Financial Services", "LICI": "Insurance",
    "LODHA": "Real Estate", "LT": "Infrastructure",
    "LTF": "Financial Services", "LTM": "IT & Technology",
    "LUPIN": "Pharma & Healthcare", "M&M": "Automobile",
    "MANAPPURAM": "Financial Services", "MANKIND": "Pharma & Healthcare",
    "MARICO": "FMCG", "MARUTI": "Automobile",
    "MAXHEALTH": "Pharma & Healthcare", "MAZDOCK": "Ship Building",
    "MCX": "Financial Services", "MFSL": "Financial Services",
    "MOTHERSON": "Auto Ancillary", "MOTILALOFS": "Financial Services",
    "MPHASIS": "IT & Technology", "MUTHOOTFIN": "Financial Services",
    "NAM-INDIA": "Financial Services", "NATIONALUM": "Metals",
    "NAUKRI": "IT & Technology", "NBCC": "Construction",
    "NESTLEIND": "FMCG", "NHPC": "Energy & Power",
    "NMDC": "Metals", "NTPC": "Energy & Power",
    "NUVAMA": "Financial Services", "NYKAA": "Retail",
    "OBEROIRLTY": "Real Estate", "OFSS": "IT & Technology",
    "OIL": "Oil & Gas", "ONGC": "Oil & Gas",
    "PAGEIND": "FMCG", "PATANJALI": "FMCG",
    "PAYTM": "Financial Services", "PERSISTENT": "IT & Technology",
    "PETRONET": "Oil & Gas", "PFC": "Financial Services",
    "PGEL": "Capital Goods", "PHOENIXLTD": "Real Estate",
    "PIDILITIND": "Chemicals", "PIIND": "Chemicals",
    "PNB": "Banking", "PNBHOUSING": "Financial Services",
    "POLICYBZR": "Insurance", "POLYCAB": "Capital Goods",
    "POWERGRID": "Energy & Power", "POWERINDIA": "Capital Goods",
    "PREMIERENE": "Capital Goods", "PRESTIGE": "Real Estate",
    "RADICO": "FMCG", "RBLBANK": "Banking",
    "RECLTD": "Financial Services", "RELIANCE": "Oil & Gas",
    "RVNL": "Construction", "SAIL": "Metals",
    "SAMMAANCAP": "Financial Services", "SBICARD": "Financial Services",
    "SBILIFE": "Insurance", "SBIN": "Banking",
    "SHREECEM": "Cement", "SHRIRAMFIN": "Financial Services",
    "SIEMENS": "Capital Goods", "SOLARINDS": "Chemicals",
    "SONACOMS": "Auto Ancillary", "SRF": "Chemicals",
    "SUNPHARMA": "Pharma & Healthcare", "SUPREMEIND": "Consumer Durables",
    "SUZLON": "Energy & Power", "SWIGGY": "IT & Technology",
    "TATACHEM": "Chemicals", "TATACONSUM": "FMCG",
    "TATAELXSI": "IT & Technology", "TATAPOWER": "Energy & Power",
    "TATASTEEL": "Metals", "TCS": "IT & Technology",
    "TECHM": "IT & Technology", "TIINDIA": "Auto Ancillary",
    "TITAN": "Jewellery", "TMPV": "Automobile",
    "TORNTPHARM": "Pharma & Healthcare", "TRENT": "Retail",
    "TVSMOTOR": "Automobile", "ULTRACEMCO": "Cement",
    "UNIONBANK": "Banking", "UNITDSPR": "FMCG",
    "UNOMINDA": "Auto Ancillary", "UPL": "Chemicals",
    "VBL": "FMCG", "VEDL": "Metals",
    "VMM": "FMCG", "VOLTAS": "Consumer Durables",
    "WAAREEENER": "Energy & Power", "WIPRO": "IT & Technology",
    "YESBANK": "Banking", "ZYDUSLIFE": "Pharma & Healthcare",
}

SECTORS = sorted(set(SECTOR_MAP.values()))

# Universe definitions (symbols only)
NIFTY50_SYMBOLS = {
    "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR","BHARTIARTL",
    "KOTAKBANK","ITC","LT","SBIN","WIPRO","AXISBANK","ASIANPAINT","BAJFINANCE",
    "MARUTI","TITAN","NTPC","TRENT","BAJAJFINSV","SUNPHARMA","HCLTECH","ONGC",
    "ADANIPORTS","M&M","POWERGRID","ULTRACEMCO","ADANIENT","JSWSTEEL",
    "HDFCLIFE","TATASTEEL","BAJAJ-AUTO","HINDALCO","NESTLEIND","CIPLA",
    "SBILIFE","BEL","DIVISLAB","PNB","TATACONSUM","GODREJCP","BRITANNIA","COALINDIA",
}

NIFTY100_SYMBOLS = NIFTY50_SYMBOLS | {
    "ABB","ADANIENSOL","ADANIGREEN","AMBUJACEM","APOLLOHOSP","AUBANK",
    "BANKBARODA","BOSCHLTD","BPCL","BSE","CANBK","CHOLAFIN",
    "DABUR","DALBHARAT","DIXON","DMART","DRREDDY","EICHERMOT","EXIDEIND",
    "FEDERALBNK","GAIL","GRASIM","HAVELLS","HDFCAMC","HEROMOTOCO","HINDPETRO",
    "HINDZINC","ICICIGI","ICICIPRULI","INDIGO","INDUSINDBK","INDUSTOWER","IOC",
    "IREDA","IRFC","JIOFIN","JSWENERGY","JUBLFOOD","LICI","LODHA","LTF",
    "MANKIND","MARICO","MCX","MOTHERSON","MPHASIS","NHPC","NYKAA",
    "OBEROIRLTY","PAGEIND","PERSISTENT","PETRONET","PFC","PIDILITIND","PIIND",
    "POLYCAB","RBLBANK","RECLTD","SBICARD","SHREECEM","SHRIRAMFIN","SIEMENS",
    "SRF","TATAPOWER","TATACHEM","TECHM","TORNTPHARM","TVSMOTOR","UPL","VEDL",
    "VOLTAS","YESBANK","ZYDUSLIFE",
}

FULL_FO_SYMBOLS = NIFTY100_SYMBOLS | set(SYMBOLS_DATA.keys())

# Margin-filtered universe (dynamically built from live prices)
# Default: start with Full F&O (will be filtered on first use)
def _load_margin_universe_from_cache():
    """Load the most recent cached margin universe if available."""
    cache_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'market_data', 'margin_universe.json')
    if os.path.exists(cache_file):
        try:
            with open(cache_file) as f:
                cached = json.load(f)
            symbols = cached.get('symbols', [])
            if symbols:
                return set(symbols)
        except Exception:
            pass
    return set(SYMBOLS_DATA.keys())

MARGIN_FILTERED_UNIVERSE = _load_margin_universe_from_cache()

UNIVERSE_MAP = {"Nifty 50": NIFTY50_SYMBOLS, "Nifty 100": NIFTY100_SYMBOLS,
                "Nifty 200": FULL_FO_SYMBOLS, "Full F&O": FULL_FO_SYMBOLS,
                "Margin < 200K": MARGIN_FILTERED_UNIVERSE}
UNIVERSE_NAMES = list(UNIVERSE_MAP.keys())
UNIVERSE_DEFAULT = "Margin < 200K"


# ── IntraPortfolio-compatible structures ─────────────────────

NIFTY200_DICT = [{"Symbol": f"{s}.NS", "LotSize": SYMBOLS_DATA[s]["lot"],
                   "TickSize": SYMBOLS_DATA[s]["tick"]}
                 for s in sorted(SYMBOLS_DATA)]

NIFTY50_DICT = [{"Symbol": f"{s}.NS", "LotSize": SYMBOLS_DATA[s]["lot"],
                  "TickSize": SYMBOLS_DATA[s]["tick"]}
                for s in sorted(SYMBOLS_DATA) if s in NIFTY50_SYMBOLS]

NIFTY200 = [e["Symbol"] for e in NIFTY200_DICT]
NIFTY50 = [e["Symbol"] for e in NIFTY50_DICT]

NIFTY200_SYMBOLS = set(SYMBOLS_DATA.keys())
NIFTY50_SYMBOLS_LIST = [f"{s}.NS" for s in sorted(NIFTY50_SYMBOLS)]

_lot_cache = {e["Symbol"]: e["LotSize"] for e in NIFTY200_DICT}
_tick_cache = {e["Symbol"]: e["TickSize"] for e in NIFTY200_DICT}


def get_tick_size(symbol):
    sym = symbol.replace(".NS", "") if symbol.endswith(".NS") else symbol
    data = SYMBOLS_DATA.get(sym)
    return data["tick"] if data else 0.05


def get_lot_size(symbol):
    data = SYMBOLS_DATA.get(symbol)
    return data["lot"] if data else None


def refresh_margin_filtered(max_margin=200000, margin_rate=0.30, cache_file=None):
    """Fetch live prices, filter by margin, update MARGIN_FILTERED_UNIVERSE.
    Returns count, or 0 on failure (previous universe is preserved)."""
    global MARGIN_FILTERED_UNIVERSE
    import yfinance as yf, pandas as pd, os, json
    from datetime import datetime
    if cache_file is None:
        cache_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'market_data', 'margin_universe.json')

    # Try cache first (less than 1 hour old)
    if os.path.exists(cache_file):
        try:
            with open(cache_file) as f:
                cached = json.load(f)
            cache_time = cached.get('date', '')
            if cache_time:
                cache_dt = datetime.strptime(cache_time, '%Y-%m-%d %H:%M')
                if (datetime.now() - cache_dt).total_seconds() < 3600:
                    MARGIN_FILTERED_UNIVERSE = set(cached.get('symbols', []))
                    return len(MARGIN_FILTERED_UNIVERSE)
        except:
            pass

    try:
        all_syms = sorted(SYMBOLS_DATA.keys())
        tickers = [s + '.NS' for s in all_syms]
        prices = {}
        for i in range(0, len(tickers), 50):
            batch = tickers[i:i+50]
            try:
                df = yf.download(batch, period='2d', interval='1d', progress=False, auto_adjust=False, group_by='ticker')
                if df.empty:
                    continue
                if isinstance(df.columns, pd.MultiIndex):
                    for t in batch:
                        sym = t.replace('.NS', '')
                        try:
                            sd = df.xs(t, axis=1, level=0)
                            c = sd['Close'].dropna()
                            if len(c) > 0:
                                prices[sym] = float(c.iloc[-1])
                        except:
                            pass
            except:
                pass

        qual = set()
        for sym in all_syms:
            price = prices.get(sym)
            if price is None:
                continue
            lot = SYMBOLS_DATA[sym]['lot']
            margin = lot * price * margin_rate
            if margin <= max_margin:
                qual.add(sym)

        MARGIN_FILTERED_UNIVERSE.clear()
        MARGIN_FILTERED_UNIVERSE.update(qual)
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, 'w') as f:
            json.dump({'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
                       'max_margin': max_margin, 'margin_rate': margin_rate,
                       'symbols': sorted(qual)}, f, indent=2)
        return len(qual)
    except Exception as e:
        print(f"  [WARN] refresh_margin_filtered failed: {e}")
        return len(MARGIN_FILTERED_UNIVERSE)


def get_margin(symbol, price, margin_rate=0.30):
    """Calculate approximate futures margin for 1 lot."""
    lot = get_lot_size(symbol)
    return lot * price * margin_rate


def filter_by_margin(symbols, price_func, max_margin=200000):
    """Return only symbols whose 1-lot margin is within max_margin."""
    result = set()
    for sym in symbols:
        price = price_func(sym)
        if price and get_margin(sym, price) <= max_margin:
            result.add(sym)
    return result
