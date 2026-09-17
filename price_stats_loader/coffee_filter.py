"""Filter a PR-stats TSV down to independent, correctly-attributed rows.

Run with the venv python (/home/tom/venv/bin/python): session_utils needs
pandas_market_calendars, which the system python3 does not have.
"""

import argparse
import sys
import pandas as pd
from pathlib import Path

# The same helper that built the capped half of the corpus — reused rather than
# reimplemented so re-capping an already-capped row is byte-identical.
from json_bodyColumn_addON import collapse_and_truncate
# The same session/exchange definitions fromPR_HighOfDay_addOn.py uses to build
# fromPR_High(%). Sharing them is the point: if the measurement window is ever
# redefined, the sibling collapse below follows it automatically instead of
# silently disagreeing with the labels.
from session_utils import (get_target_date, is_us_exchange, open_seconds_between,
                           parse_created_dt)


def _ticker_set(symbol, tickers):
    """Uppercased set of tickers from the Symbol and Tickers fields.

    Tickers may be plain comma-separated ("APPL, APPLW") or a JSON-style list
    string ('["APPL", "APPLW"]'); brackets and quotes are stripped either way.
    """
    out = set()
    for raw in [symbol] + str(tickers if pd.notna(tickers) else '').split(','):
        t = str(raw if pd.notna(raw) else '').strip().strip('[]"\'').strip().upper()
        if t:
            out.add(t)
    return out


def _tickers_overlap(a, b):
    """True when the two ticker sets refer to the same company: any identical
    ticker, or one ticker being the warrant of the other (exact base+'W' only,
    so e.g. SNOW never matches SNO)."""
    if not a or not b:
        return True
    if a & b:
        return True
    return any(t + 'W' in b for t in a) or any(t + 'W' in a for t in b)


def _pr_timestamps(df):
    """Full datetime of each PR: ArrivalDate + Created (ms precision, the PR's own
    emission time), falling back to ArrivalTime. NaT where it cannot be parsed.

    The date half matters: a time-only key carries no date, so "earliest" would
    compare wall-clock times across a multi-day file and pick the wrong row.
    """
    date = df['ArrivalDate'].astype(str) if 'ArrivalDate' in df.columns else None
    for col in ('Created', 'ArrivalTime'):
        if col not in df.columns:
            continue
        raw = df[col].astype(str)
        ts = pd.to_datetime(date + ' ' + raw if date is not None else raw,
                            errors='coerce')
        if ts.notna().any():
            return ts
    return pd.Series(pd.NaT, index=df.index)


def remove_duplicates(df, headline_window_hours=24):
    """Drop duplicate PRs, keeping the earliest row (ties -> file order).

    Pass A: rows sharing a non-empty ID. An ID identifies one article, so this
    pass is never time-scoped.
    Pass B: rows sharing a normalized (trimmed, lowercased) Headline whose ticker
    sets overlap (a warrant like APPLW counts as its base APPL) AND which land
    within `headline_window_hours` of the last kept row of that group. Without a
    window a reused headline collapses events that are weeks apart and genuinely
    distinct — monthly distributions, recurring inducement grants, repeat
    corporate updates. `headline_window_hours=None` restores that unscoped match.

    Returns (deduped df in original file order, n_dropped_id, n_dropped_headline).
    """
    ts = _pr_timestamps(df)
    order = ts.sort_values(kind='stable', na_position='last').index
    sdf = df.loc[order]

    dropped_id = 0
    if 'ID' in df.columns:
        ids = sdf['ID'].fillna('').astype(str).str.strip()
        dup_mask = ids.duplicated(keep='first') & (ids != '')
        dropped_id = int(dup_mask.sum())
        sdf = sdf[~dup_mask]

    dropped_hl = 0
    if 'Headline' in df.columns:
        hl_norm = sdf['Headline'].fillna('').astype(str).str.strip().str.lower()
        symbols = sdf['Symbol'] if 'Symbol' in sdf.columns else pd.Series('', index=sdf.index)
        tickers = sdf['Tickers'] if 'Tickers' in sdf.columns else pd.Series('', index=sdf.index)
        window = (pd.Timedelta(hours=headline_window_hours)
                  if headline_window_hours is not None else None)
        keep_idx = []
        # normalized headline -> [(ticker set, timestamp of that group's last kept row)]
        kept_by_headline = {}
        # ts.loc[sdf.index], not ts.loc[order]: the ID pass above already removed
        # rows from sdf, so the full-frame series would zip out of alignment.
        for idx, hl, sym, tks, t in zip(sdf.index, hl_norm, symbols, tickers,
                                        ts.loc[sdf.index]):
            tset = _ticker_set(sym, tks)
            kept_groups = kept_by_headline.setdefault(hl, [])
            match = next((pos for pos, (kset, _) in enumerate(kept_groups)
                          if hl and _tickers_overlap(tset, kset)), None)
            if match is None:
                kept_groups.append((tset, t))
            else:
                kts = kept_groups[match][1]
                # A row we cannot place in time is never called a duplicate, the
                # same always-keep-NaT rule remove_same_day_siblings uses.
                if window is None or (pd.notna(t) and pd.notna(kts) and t - kts < window):
                    dropped_hl += 1
                    continue
                # Outside the window: a distinct event, and it becomes the anchor
                # so a re-publish of *it* is measured from here.
                kept_groups[match] = (kept_groups[match][0], t)
            keep_idx.append(idx)
        sdf = sdf.loc[keep_idx]

    return df[df.index.isin(sdf.index)], dropped_id, dropped_hl


def _pr_sessions(df):
    """Per row: (outcome session, tz-aware US/Eastern PR datetime).

    Both come from parse_created_dt + get_target_date, the same pair
    fromPR_HighOfDay_addOn uses to place a PR's high window — so two rows land in
    the same session here exactly when their fromPR_High(%) is measured against the
    same 8 PM ET close.

    Rows whose Created cannot be parsed fall back to (ArrivalDate string, None):
    the old calendar-day grouping, and a None datetime that the always-keep rule in
    remove_sibling_prs treats as unplaceable in time.
    """
    sessions, moments = [], []
    dates = df['ArrivalDate'].astype(str) if 'ArrivalDate' in df.columns else None
    arrivals = (df['ArrivalTime'].astype(str) if 'ArrivalTime' in df.columns
                else pd.Series('', index=df.index))
    created = (df['Created'].astype(str) if 'Created' in df.columns
               else pd.Series('', index=df.index))
    for i, (c, a) in enumerate(zip(created, arrivals)):
        day = dates.iloc[i] if dates is not None else ''
        try:
            dt = parse_created_dt(c, a, day)
            sessions.append(get_target_date(dt))
            moments.append(dt)
        except (ValueError, TypeError):
            sessions.append(day)
            moments.append(None)
    return pd.Series(sessions, index=df.index), pd.Series(moments, index=df.index)


def remove_sibling_prs(df, cooloff_minutes):
    """Collapse clusters of PRs from one company in one trading session, keeping
    the earliest.

    Sibling PRs are not independent observations: their fromPR_High(%) windows all
    end at the same 8 PM ET close, so a filler PR can inherit the tail of a move an
    earlier PR caused. The cluster key is the *session* the outcome is measured in
    (get_target_date), not ArrivalDate — a PR at 16:15 and one at 09:00 the next
    morning are different calendar days but the same session, and the second one's
    news lands inside the first one's measurement window.

    A row is kept when it is the first of its (Symbol, session), or when it lands at
    least `cooloff_minutes` after the last *kept* row of that company-session
    (comparing against the last kept row, not the previous one, so a chain of
    closely-spaced PRs collapses to a single survivor). `cooloff_minutes=None`
    collapses the whole session to one PR.

    The gap is measured in market-open minutes (open_seconds_between), not wall
    clock: an overnight close is long on the clock but gives the first PR's move no
    chance to decay, so those two PRs are adjacent, not independent.

    The earliest PR of a cluster is kept rather than the biggest mover: picking by
    fromPR_High(%) would be selection on the outcome variable, and the earliest PR
    is the only one whose baseline bar is uncontaminated by a sibling.

    Rows with an unparseable timestamp are always kept. Returns
    (df in original file order, n_dropped, n_company_sessions_affected).
    """
    if 'Symbol' not in df.columns or 'ArrivalDate' not in df.columns:
        return df, 0, 0

    # Order on _pr_timestamps, unchanged, so the sort is identical to the dedup pass
    # above; _pr_sessions supplies the grouping key and the market-open clock.
    ts = _pr_timestamps(df)
    order = ts.sort_values(kind='stable', na_position='last').index
    sdf = df.loc[order]
    sessions, moments = _pr_sessions(sdf)

    cooloff_s = cooloff_minutes * 60 if cooloff_minutes is not None else None
    last_kept = {}   # (SYMBOL, session) -> datetime of the last kept PR
    dropped_keys = set()
    keep_idx = []
    for idx, sym, sess, t in zip(sdf.index,
                                 sdf['Symbol'].astype(str).str.strip().str.upper(),
                                 sessions, moments):
        key = (sym, sess)
        prev = last_kept.get(key)
        if prev is not None and t is not None and (
                cooloff_s is None or open_seconds_between(prev, t) < cooloff_s):
            dropped_keys.add(key)
            continue
        if t is not None:
            last_kept[key] = t
        keep_idx.append(idx)

    return df[df.index.isin(keep_idx)], len(df) - len(keep_idx), len(dropped_keys)


def remove_non_us_rows(df):
    """Drop rows whose Exchange is a market outside the US data subscription.

    These are ticker collisions with foreign listings: the PR is from the foreign
    issuer (Corero Network Security under CNSP, Prosafe SE under PRSO, Atos SE under
    ATOS) while Float, LastDailyClose and every price-derived column belong to the US
    company that shares the symbol. fromPR_HighOfDay_addOn already refuses to score
    them ('skip'), but they still carry wrong features into any text or class work.

    A blank Exchange is kept — it does not prove the row is foreign, and the add-on
    makes the same call. Returns (df, n_dropped).
    """
    if 'Exchange' not in df.columns:
        return df, 0
    exch = df['Exchange'].fillna('').astype(str).str.strip()
    drop = exch.ne('') & ~exch.map(is_us_exchange)
    return df[~drop], int(drop.sum())


def cap_body_words(df, max_words):
    """Truncate Body to its first `max_words` words, reproducing the corpus cap.

    Bodies were capped at 400 words through 2026-06-30 and uncapped from 07-01, so a
    train/val split straddling that date scores a model on ~2x longer documents than
    it trained on, inflating every count-based feature. Re-capping corpus-wide puts
    the whole file back into one length regime.

    Delegates to json_bodyColumn_addON.collapse_and_truncate — the helper that built
    the capped half — so rows already at or under the cap pass through unchanged and
    re-running is a no-op. Returns (df, n_rows_shortened).
    """
    if 'Body' not in df.columns:
        print("ERROR: column 'Body' not found in input file.", file=sys.stderr)
        sys.exit(1)

    df = df.copy()
    # fillna first: collapse_and_truncate's `text or ''` guard catches None (its JSON
    # callers) but not a float NaN, which is truthy and would stringify to 'nan'.
    body = df['Body'].fillna('')
    capped = body.map(lambda t: collapse_and_truncate(t, max_words))
    n_changed = int((capped != body).sum())
    df['Body'] = capped
    return df, n_changed


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Filter a news TSV: duplicate removal, Float and/or DailyHigh($) caps, '
            'excluded-strings on Headline.\n\n'
            'Rows that exceed the specified upper bounds are removed.\n'
            'When a filter is active, rows with a missing value in that column are also dropped.\n'
            'Duplicates (same ID, or same Headline for the same company incl. warrant '
            'tickers within --dup-window hours) are removed by default; the earliest '
            'row is kept.\n'
            'Rows on a non-US exchange are always dropped: those are ticker collisions '
            'with foreign listings, where the PR is one company and the price columns '
            'are another.\n'
            'With --pr-cooloff, clusters of PRs from one company in one trading '
            'session are also collapsed to their earliest row, so overlapping '
            'fromPR_High(%) windows do not produce dependent rows.\n'
            'With --max-body-words, Body is truncated to a fixed word count, undoing '
            'the 2026-07-01 break where the corpus stopped capping bodies at 400 words.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--input', '-i',
        required=True,
        metavar='FILE',
        dest='input_file',
        help='Path to the input .tsv file.',
    )
    parser.add_argument(
        '--output', '-o',
        required=True,
        metavar='FILE',
        dest='output_file',
        help='Path for the filtered output .tsv file.',
    )
    parser.add_argument(
        '--MaxFloat',
        type=float,
        default=None,
        metavar='N',
        help='Keep rows where Float <= N (millions). Rows with no Float value are dropped.',
    )
    parser.add_argument(
        '--maxDailyHigh',
        type=float,
        default=None,
        metavar='N',
        help='Keep rows where DailyHigh($) <= N. Rows with no DailyHigh($) value are dropped.',
    )
    parser.add_argument(
        '--remove-duplicates',
        choices=['true', 'false'],
        default='true',
        metavar='<true|false>',
        help=(
            'Remove duplicate rows (default: true). Two passes, both keeping the '
            'earliest row: same ID, then same Headline '
            '(case-insensitive, trimmed) with overlapping Symbol/Tickers, where a '
            'warrant ticker (base+W) matches its base company. The headline pass is '
            'limited to --dup-window hours.'
        ),
    )
    parser.add_argument(
        '--dup-window',
        default='24',
        metavar='HOURS',
        dest='dup_window',
        help=(
            'Only treat two rows with the same Headline as duplicates when they arrive '
            'within HOURS of each other (default: 24). Recurring PRs that reuse a '
            'headline — monthly distributions, inducement grants, repeat corporate '
            'updates — are then kept as the distinct events they are. Pass "none" to '
            'match on headline regardless of date. Does not affect the same-ID pass.'
        ),
    )
    parser.add_argument(
        '--pr-cooloff',
        nargs='?',
        const='day',
        default=None,
        metavar='MINUTES',
        dest='pr_cooloff',
        help=(
            'Collapse sibling PRs from one company within one trading session, keeping '
            'the earliest of each cluster (never the biggest mover — that would be '
            'selection on the outcome). Pass a gap in MINUTES OF MARKET-OPEN TIME '
            '(recommended: 240; PRs further apart than that measure as independent) — '
            'an overnight or weekend close counts as zero elapsed. Or pass a bare '
            '--pr-cooloff to keep only one PR per company per session. The session is '
            'the one fromPR_High(%) is measured in, so a 16:15 PR groups with the next '
            'morning, not with its own calendar day. Omit the flag to disable.'
        ),
    )
    parser.add_argument(
        '--max-body-words',
        nargs='?',
        type=int,
        const=400,
        default=None,
        metavar='N',
        dest='max_body_words',
        help=(
            'Truncate Body to its first N words (bare flag: 400). Reproduces the cap '
            'that applied to the corpus through 2026-06-30 and stopped on 07-01, so a '
            'train/val split straddling that date is not scored on 2x longer bodies. '
            'Omit the flag to leave Body untouched.'
        ),
    )
    parser.add_argument(
        '--string-to-exclude',
        default=None,
        metavar='FILE',
        dest='exclude_file',
        help=(
            'Path to a text file with one string per line. Rows whose Headline '
            'contains any of these strings (case-insensitive) are dropped.'
        ),
    )
    args = parser.parse_args()

    # --pr-cooloff: absent -> off; bare flag -> None (whole session); else minutes.
    cooloff_on = args.pr_cooloff is not None
    cooloff_minutes = None
    if cooloff_on and args.pr_cooloff != 'day':
        try:
            cooloff_minutes = float(args.pr_cooloff)
        except ValueError:
            print(f"ERROR: --pr-cooloff expects a number of minutes or no value, "
                  f"got {args.pr_cooloff!r}.", file=sys.stderr)
            sys.exit(1)
        if cooloff_minutes <= 0:
            print("ERROR: --pr-cooloff must be greater than 0.", file=sys.stderr)
            sys.exit(1)

    # --dup-window: "none" -> unscoped headline match; else a positive hour count.
    dup_window = None
    if str(args.dup_window).strip().lower() != 'none':
        try:
            dup_window = float(args.dup_window)
        except ValueError:
            print(f"ERROR: --dup-window expects a number of hours or 'none', "
                  f"got {args.dup_window!r}.", file=sys.stderr)
            sys.exit(1)
        if dup_window <= 0:
            print("ERROR: --dup-window must be greater than 0 (use 'none' to disable "
                  "the window).", file=sys.stderr)
            sys.exit(1)

    if args.max_body_words is not None and args.max_body_words <= 0:
        print("ERROR: --max-body-words must be greater than 0.", file=sys.stderr)
        sys.exit(1)

    input_path = Path(args.input_file)
    if not input_path.is_file():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_csv(input_path, sep='\t', dtype=str)
    total = len(df)

    dropped_id = dropped_hl = 0
    if args.remove_duplicates == 'true':
        df, dropped_id, dropped_hl = remove_duplicates(df, dup_window)

    # Before the sibling collapse: a foreign-listing row is a *different company*,
    # so it never touched this symbol's tape and must not anchor a cluster. This is
    # the opposite of the --string-to-exclude case below.
    df, dropped_non_us = remove_non_us_rows(df)

    # After dedup (duplicate rows are the same event, not siblings, and would
    # inflate the clusters) but before --string-to-exclude: contamination comes
    # from the market event, so a PR that a later filter drops still occupied the
    # tape and must still be counted as the head of its cluster.
    dropped_sib = sib_days = 0
    if cooloff_on:
        df, dropped_sib, sib_days = remove_sibling_prs(df, cooloff_minutes)

    if args.MaxFloat is not None:
        if 'Float' not in df.columns:
            print("ERROR: column 'Float' not found in input file.", file=sys.stderr)
            sys.exit(1)
        df['Float'] = pd.to_numeric(df['Float'], errors='coerce')
        df = df[df['Float'] <= args.MaxFloat]

    if args.maxDailyHigh is not None:
        col = 'DailyHigh($)'
        if col not in df.columns:
            print(f"ERROR: column '{col}' not found in input file.", file=sys.stderr)
            sys.exit(1)
        df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df[df[col] <= args.maxDailyHigh]

    if args.exclude_file is not None:
        exclude_path = Path(args.exclude_file)
        if not exclude_path.is_file():
            print(f"ERROR: exclude file not found: {exclude_path}", file=sys.stderr)
            sys.exit(1)
        if 'Headline' not in df.columns:
            print("ERROR: column 'Headline' not found in input file.", file=sys.stderr)
            sys.exit(1)
        # Load excluded strings the same way Orchestrator5.0 does: one per line,
        # stripped, blanks skipped, deduped into a set.
        excluded = {
            line.strip() for line in exclude_path.read_text().splitlines()
            if line.strip()
        }
        if excluded:
            # Match exactly like O5: case-insensitive substring on the Headline
            # only (never the body) — `s.lower() in headline.lower()`.
            excluded_lower = [s.lower() for s in excluded]
            hl_lower = df['Headline'].fillna('').str.lower()
            mask = hl_lower.apply(lambda h: any(s in h for s in excluded_lower))
            df = df[~mask]

    # Last, on the surviving rows only: this is a transformation of the kept text,
    # not a row filter, so there is no point capping bodies that get dropped above.
    bodies_capped = 0
    if args.max_body_words is not None:
        df, bodies_capped = cap_body_words(df, args.max_body_words)

    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, sep='\t', index=False)

    hl_window = '' if dup_window is None else f' within {dup_window:g}h'
    dedup_note = (
        f"{dropped_id + dropped_hl} duplicates dropped "
        f"({dropped_id} dup-ID, {dropped_hl} dup-headline{hl_window}) → "
        if args.remove_duplicates == 'true' else ''
    )
    non_us_note = (
        f"{dropped_non_us} non-US-exchange rows dropped → " if dropped_non_us else ''
    )

    if cooloff_on:
        window = ('whole session' if cooloff_minutes is None
                  else f'{cooloff_minutes:g}m market-open cooloff')
        sibling_note = (
            f"{dropped_sib} sibling PRs dropped "
            f"({window}, {sib_days} company-sessions) → "
        )
    else:
        sibling_note = ''

    body_note = (
        f"{bodies_capped} bodies capped to {args.max_body_words} words → "
        if args.max_body_words is not None else ''
    )

    print(f"{total} rows in → {dedup_note}{non_us_note}{sibling_note}{body_note}"
          f"{len(df)} rows out → {output_path}")


if __name__ == '__main__':
    main()
