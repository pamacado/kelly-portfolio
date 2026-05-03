import requests
import json
from typing import Dict, List, Optional

class PolymarketClient:
    """
    Advanced Python client for Polymarket.
    Handles events with multiple markets and outcomes, fixing stringified JSON bugs,
    and calculating true Bid-Ask spreads mathematically.
    """

    def __init__(self):
        self.gamma_url = "https://gamma-api.polymarket.com/events"
        self.clob_url = "https://clob.polymarket.com/book"

    def extract_slug_from_url(self, url: str) -> str:
        """Extracts the slug from a standard Polymarket URL."""
        clean_url = url.strip().rstrip('/')
        slug = clean_url.split('/')[-1].split('?')[0]
        return slug

    def get_all_options_from_url(self, polymarket_url: str) -> List[Dict]:
        """
        Scans an event and returns ALL available tradable options.
        """
        slug = self.extract_slug_from_url(polymarket_url)
        
        try:
            gamma_res = requests.get(f"{self.gamma_url}?slug={slug}", timeout=5)
            gamma_res.raise_for_status()
            data = gamma_res.json()
            
            if not data:
                print(f"[!] Warning: Event not found for slug: {slug}")
                return []
                
            event = data[0]
            available_options = []
            
            # Loop through all markets inside the event
            for market in event.get("markets", []):
                question = market.get("question", "Unknown Question")
                end_date = market.get("endDate", "Unknown Date")
                
                # Parse stringified lists into actual Python lists
                outcomes = market.get("outcomes", [])
                if isinstance(outcomes, str):
                    try:
                        outcomes = json.loads(outcomes)
                    except json.JSONDecodeError:
                        outcomes = []
                
                token_ids = market.get("clobTokenIds", [])
                if isinstance(token_ids, str):
                    try:
                        token_ids = json.loads(token_ids)
                    except json.JSONDecodeError:
                        token_ids = []
                        
                # Fallback if clobTokenIds is empty
                if not token_ids and market.get("tokens"):
                    token_ids = [t.get("token_id") for t in market.get("tokens", [])]

                # Map each outcome to its respective Token ID
                for i, outcome in enumerate(outcomes):
                    if i < len(token_ids):
                        available_options.append({
                            "market_question": question,
                            "outcome": outcome,
                            "token_id": token_ids[i],
                            "end_date": end_date,
                            "event_slug": slug
                        })
                        
            return available_options

        except requests.exceptions.RequestException as e:
            print(f"[X] Error in Gamma API: {e}")
            return []

    def get_order_book(self, token_id: str) -> Optional[Dict]:
        """
        Given a specific token_id, fetches the live Order Book.
        Calculates the TRUE best bid and best ask mathematically.
        """
        try:
            clob_res = requests.get(f"{self.clob_url}?token_id={token_id}", timeout=5)
            clob_res.raise_for_status()
            clob_data = clob_res.json()
            
            bids = clob_data.get("bids", [])
            asks = clob_data.get("asks", [])
            
            if not bids or not asks:
                return None
                
            # The Fix: Find the absolute minimum ask and maximum bid
            best_ask = min(float(ask["price"]) for ask in asks)
            best_bid = max(float(bid["price"]) for bid in bids)
            
            spread = round(best_ask - best_bid, 4)

            return {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread_cents": spread,
                "is_tradable": spread < 0.05
            }

        except requests.exceptions.RequestException:
            return None


# ==========================================
# Testing Area
# ==========================================
if __name__ == "__main__":
    client = PolymarketClient()
    
    # Using your exact URL
    test_url = "https://polymarket.com/sports/nba/nba-mia-cha-2026-03-17"
    
    print(f"\n[*] Scanning URL: {test_url}\n")
    options = client.get_all_options_from_url(test_url)
    
    if not options: 
        print("[X] No options found. Check if the URL is correct.")
    else:
        print(f"[+] Found {len(options)} tradable options:\n")
        
        for i, opt in enumerate(options):
            print(f"  [{i}] {opt['market_question']} -> Bet on: '{opt['outcome']}'")
        
        print("\n" + "-"*50)
        print("[*] Simulating user selecting option [0]...\n")
        
        chosen_option = options[0]
        market_data = client.get_order_book(chosen_option["token_id"])
        
        if market_data:
            print(f"📌 Market: {chosen_option['market_question']}")
            print(f"👉 Outcome: {chosen_option['outcome']}")
            print(f"🛒 Best Ask (Buy Price):  ${market_data['best_ask']}")
            print(f"🤝 Best Bid (Sell Price): ${market_data['best_bid']}")
            print(f"💸 Spread:                ${market_data['spread_cents']}")
        else:
            print("[X] The Order Book is empty. The market lacks liquidity or is closed.")