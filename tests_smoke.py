from database import initialize_database
from market_data import get_history
from forecasting import forecast_price
from engine import analyze_market

def main():
    initialize_database()
    h=get_history("SPY","6mo","1d")
    if not h.empty:
        s=analyze_market("SPY",h,0.0)
        f=forecast_price(h,5)
        print("signal",s.action if s else None,"forecast",f.target_price if f else None)
    else:
        print("Market data unavailable during smoke test.")
if __name__=="__main__": main()
