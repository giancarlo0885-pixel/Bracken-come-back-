from __future__ import annotations
import importlib

MODULES = {
    "Whale Surveillance":"whale_monitor",
    "Dark Pool":"dark_pool_monitor",
    "Options Flow":"options_flow",
    "ETF Flow":"etf_flow",
    "Insider Activity":"insider_monitor",
    "Congress Trading":"congress_monitor",
    "Economic Calendar":"economic_calendar",
    "Earnings Calendar":"earnings_calendar",
    "Regulatory Alerts":"regulatory_monitor",
    "Geopolitical Alerts":"geopolitical_monitor",
}

def collect_all():
    out={}
    for label,module_name in MODULES.items():
        try:
            module=importlib.import_module(module_name)
            out[label]=module.fetch()
        except Exception as exc:
            out[label]=type("Result",(),{"available":False,"provider":"Error","records":[],"message":str(exc)})()
    return out
