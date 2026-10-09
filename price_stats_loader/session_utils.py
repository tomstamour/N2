"""Shared US-trading-session and exchange helpers for the price_stats_loader tools.

These used to live in fromPR_HighOfDay_addOn.py, which is the script that fills
fromPR_High(%). They moved here so tools that do not talk to the Gateway can reuse
the *exact* definitions the labeller uses — importing fromPR_HighOfDay_addOn would
pull in ib_insync, which a pure-pandas tool like coffee_filter.py has no use for.

The one rule worth remembering: fromPR_High(%) is measured over a trading *session*
ending at 8 PM ET, not over a calendar day. get_target_date() is what decides which
session a PR belongs to, so anything that groups PRs by "the same day" should group
by get_target_date() instead.

Needs pandas_market_calendars (venv only — see scripts/CLAUDE.md).
"""

import pandas_market_calendars as mcal
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

# US market closes at 4 PM ET; extended session runs 4 AM - 8 PM ET.
MARKET_CLOSE_ET_HOUR = 16
EXTENDED_OPEN_ET_HOUR = 4
EXTENDED_CLOSE_ET_HOUR = 20

EASTERN = ZoneInfo('America/New_York')

# Exchange names as they appear in the TSV that are covered by the US data subscription
US_EXCHANGES = {
    'NASDAQ', 'NYSE', 'NYSE AMERICAN', 'NYSE ARCA', 'NYSE MKT', 'AMEX', 'CBOE',
}


def is_us_exchange(exchange_val) -> bool:
    """Return True if the exchange value is a supported US market."""
    return str(exchange_val).strip().upper() in {e.upper() for e in US_EXCHANGES}


# US equity trading calendar (NYSE/Nasdaq). Used to skip weekends AND holidays
# (e.g. Memorial Day) so target dates always land on a real trading session.
_XNYS = mcal.get_calendar('XNYS')

# valid_days() is a relatively costly call and the callers below hit it once per
# row; a corpus of thousands of PRs spans only a few hundred distinct dates, so
# memoising by date collapses that to one lookup per calendar day.
_trading_day_cache: dict[date, bool] = {}
_next_trading_day_cache: dict[date, date] = {}


def is_trading_day(d: date) -> bool:
    """Return True if d is a US equity trading session (not weekend/holiday)."""
    cached = _trading_day_cache.get(d)
    if cached is None:
        cached = len(_XNYS.valid_days(start_date=d, end_date=d)) > 0
        _trading_day_cache[d] = cached
    return cached


def next_trading_day(d: date) -> date:
    """Return the first US trading session strictly after d."""
    cached = _next_trading_day_cache.get(d)
    if cached is None:
        sched = _XNYS.valid_days(start_date=d + timedelta(days=1),
                                 end_date=d + timedelta(days=10))
        cached = sched[0].date()
        _next_trading_day_cache[d] = cached
    return cached


def parse_created_dt(created_str: str, arrival_str: str,
                     arrival_date_str: str) -> datetime:
    """Combine the Created time-of-day (US/Eastern) with the row's date.

    Created holds only 'HH:MM:SS.mmm'; the date comes from ArrivalTime when it
    is a full ISO datetime ('2026-05-03 20:45:01'), otherwise from ArrivalDate.
    Returns a tz-aware US/Eastern datetime. Raises ValueError if unparseable.
    """
    created = created_str.strip()
    if not created:
        raise ValueError('empty Created value')

    arrival = arrival_str.strip()
    if arrival[:5].count('-'):
        day = datetime.fromisoformat(arrival).date()
    elif arrival_date_str.strip():
        day = date.fromisoformat(arrival_date_str.strip())
    else:
        raise ValueError('no date available (ArrivalTime time-only, no ArrivalDate)')

    tod = datetime.strptime(created, '%H:%M:%S.%f').time()
    return datetime.combine(day, tod, tzinfo=EASTERN)


def get_target_date(created_dt: datetime) -> date:
    """Return the trading session whose end (8 PM ET) closes the high window.

    Created before the 4 PM close on a trading day -> that same day. Otherwise
    (after close, or a weekend/holiday) -> the next trading session.
    """
    created_date = created_dt.date()
    if created_dt.hour < MARKET_CLOSE_ET_HOUR and is_trading_day(created_date):
        return created_date
    return next_trading_day(created_date)


def open_seconds_between(start: datetime, end: datetime) -> int:
    """Seconds between start and end that fall within extended trading sessions
    (4 AM-8 PM ET on US trading days). Overnight closes (8 PM-4 AM) and
    non-trading days contribute zero, so the result reflects only time the
    market was open (extended hours included)."""
    if end <= start:
        return 0
    total = 0
    day = start.date()
    while day <= end.date():
        if is_trading_day(day):
            sess_open = datetime(day.year, day.month, day.day,
                                 EXTENDED_OPEN_ET_HOUR, tzinfo=EASTERN)
            sess_close = datetime(day.year, day.month, day.day,
                                  EXTENDED_CLOSE_ET_HOUR, tzinfo=EASTERN)
            lo = max(start, sess_open)
            hi = min(end, sess_close)
            if hi > lo:
                total += int((hi - lo).total_seconds())
        day += timedelta(days=1)
    return total
