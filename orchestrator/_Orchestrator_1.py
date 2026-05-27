import sys                                                                                                     
sys.path.insert(0, '/home/tom/Documents/ibkr_scripts/N1/scripts/universe_finder')
sys.path.insert(0, '/home/tom/Documents/ibkr_scripts/N1/scripts/newswatcher2')
#import universe_finder                                                                                        
#import stocks_list_fetch                                                                                      
import NewsWatcher2 as nw 
import yfinance_stock_universe

symbols = yfinance_stock_universe.fetch(max_market_cap=300)

# Fetch from both ETFs (SMMD + IWC). Logs are written to ./runs/17-Mar-2026/stocks_list_fetch.log relative to wherever you run from.                                                                       
# symbols = stocks_list_fetch.fetch(['SMMD', 'IWC']) # This function was teching the symbols from the two indexes and most of the small caps were not present in those indexes. 
#print(symbols)
#print(f"Total: {len(symbols)} symbols")

# Filter the stock list with the universe_finder function. 
#universe_finder.start(watchlist_path = symbols, max_institution_pct = 20, max_float_m = 20, max_price = 10, refresh_minutes = 1440)

# Blocks until first fetch done, then returns immediately on subsequent calls
# The universe_finder.get() function is too long and mises some symbols due to yfinance limits
#symbols = universe_finder.get_universe()
# → ['HOOD', 'CLOV', ...]

nw.start(stock_universe=symbols,                                                                                                                                                                                                                 
    black_list="/home/tom/Documents/ibkr_scripts/N1/scripts/newswatcher2/black_list.csv",                                                                                                                                                                                                                            
    blacklist_expiry_days=15,                                                                                                                                                                                                                                 
    api_keys="/home/tom/Documents/ibkr_scripts/N1/scripts/newswatcher2/alpaca_API-Keys.txt",
    flush_interval_seconds=3600,
    news_df_dir= "/home/tom/Documents/ibkr_scripts/N1/scripts/newswatcher2/outputs",
    excluded_strings=["halted", "halt", "trading suspended",
                        "nasdaq halt", "nyse halt", "shares are trading"])

