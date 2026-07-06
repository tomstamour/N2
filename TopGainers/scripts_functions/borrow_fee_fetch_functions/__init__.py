"""
Borrow Fee Fetch Functions

This package contains modules for fetching stock borrow fee data from multiple sources.

Modules:
    borrow_fee_ftp_fetcher: Fetches borrow fee data from IBKR's official FTP server
    borrow_fee_iborrowdesk_fetcher: Fetches borrow fee data from iBorrowDesk's free JSON API
    borrow_fee_orchestrator: Multi-source orchestrator that coordinates FTP + iBorrowDesk fetchers

Usage:
    # Import individual fetchers
    from borrow_fee_fetch_functions.borrow_fee_ftp_fetcher import IBKRBorrowFeeFetcher
    from borrow_fee_fetch_functions.borrow_fee_iborrowdesk_fetcher import IBorrowDeskFetcher

    # Or run the orchestrator directly via command line
    python3 scripts_functions/borrow_fee_fetch_functions/borrow_fee_orchestrator.py --input <GTT_file>
"""

__version__ = "1.0.0"
__all__ = ["borrow_fee_ftp_fetcher", "borrow_fee_iborrowdesk_fetcher", "borrow_fee_orchestrator"]
