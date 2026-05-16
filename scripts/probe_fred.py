"""Search FRED for current/active series for leading indicators."""
import os
from dotenv import load_dotenv
from fredapi import Fred

load_dotenv()
fred = Fred(api_key=os.environ["FRED_API_KEY"])


def search(query: str, max_results: int = 8):
    print(f"\n=== Search: {query!r} ===")
    try:
        res = fred.search(query)
        if res is None or res.empty:
            print("  (no results)")
            return
        for sid, row in res.head(max_results).iterrows():
            title = row.get("title", "")[:80]
            last = row.get("observation_end", "")
            freq = row.get("frequency_short", "")
            popularity = row.get("popularity", 0)
            print(f"  {sid:30s}  pop={popularity:>3}  freq={freq:>2}  end={last}  {title}")
    except Exception as e:
        print(f"  ERR: {e}")


# Find Philly Fed subcomponents (new orders, inventories)
search("Philadelphia Fed new orders manufacturing")
search("Philadelphia Fed inventories manufacturing")

# Find updated leading indices
search("Composite Leading Indicator United States")
search("S&P Global PMI United States")

# FCI-G
search("Financial Conditions Impulse Growth")
search("FCI-G")
