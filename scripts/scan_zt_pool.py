#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily scanner for A-share "yaogu" (limit-up streak / theme speculation) names.

Scores every name in the Eastmoney limit-up pool with the rules calibrated on
real base rates (see docs/01_base_rate_study_2026-10.md):

    first board -> next-day limit-up 17.0%  (n=506)
    2 boards    -> next-day limit-up 36.1%  (n=83)
    3 boards    -> next-day limit-up 40.6%  (n=32)
    5 boards+   -> next-day limit-up 50.0%  (n=14)

Usage:
    python3 scripts/scan_zt_pool.py                    # today
    python3 scripts/scan_zt_pool.py 20261113           # a specific trading day
    python3 scripts/scan_zt_pool.py 20261113 --top 20  # show more rows
    python3 scripts/scan_zt_pool.py 20261113 --json out/pick_20261113.json

Data source: Eastmoney push2ex limit-up / broken-board / limit-down pools.
Standard library only, no third-party dependencies.
"""
import argparse
import datetime
import json
import os
import sys
import time
import unicodedata
import urllib.request
from collections import Counter

UA = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'}
UT = '7eea3edcaed734bea9cbfc24409ed989'
POOL_URL = ('https://push2ex.eastmoney.com/%s?ut=%s&dpt=wz.ztzt'
            '&Pageindex=0&pagesize=500&sort=%s&date=%s')
# The limit-down pool only answers to sort=fund:asc. Reusing the limit-up
# sort (fbt:asc) returns {"tc": N, "pool": []} -- a total with an empty pool,
# which looks like a dead endpoint but is just the wrong sort key.
SORTS = {'getTopicZTPool': 'fbt%3Aasc', 'getTopicZBPool': 'fbt%3Aasc',
         'getTopicDTPool': 'fund%3Aasc'}

# Base rates measured on 2026-09-23 .. 2026-10-20 (14 trading days).
BENCH = ('benchmarks (14 trading days, 2026-09-23..2026-10-20): '
         'first board -> 17.0% | 2 boards -> 36.1% | 3 boards -> 40.6% | '
         '4 boards -> 42.9% | 5+ -> 50.0%')
BROKE_RELIMIT = 4.3  # next-day re-limit rate of names that broke today (n=258)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def jget(url, tries=3):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            raw = urllib.request.urlopen(req, timeout=20).read()
            return json.loads(raw.decode('utf-8', 'ignore'))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.0)
    raise SystemExit('ERROR fetching %s -> %s' % (url[:90], last))


def pool(api, date):
    d = jget(POOL_URL % (api, UT, SORTS.get(api, 'fbt%3Aasc'), date)).get('data') or {}
    return d.get('pool') or []


def hhmm(fbt):
    s = str(int(fbt)).zfill(6)
    return s[:2] + ':' + s[2:4]


def dwidth(text):
    """Display width: CJK characters count as 2 columns."""
    return sum(2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
               for ch in text)


def fit(text, width):
    """Truncate / right-pad text to an exact display width."""
    text = str(text)
    out = ''
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
        if dwidth(out) + w > width:
            break
        out += ch
    return out + ' ' * (width - dwidth(out))


def fnum(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------- #
# scoring (max theoretical = 15)
# --------------------------------------------------------------------------- #
def score(x, sector_count):
    """Return (score, grade, flags). Thresholds come from the base-rate tables."""
    s = 0
    flags = []

    # seal time: 09:25-09:35 -> 26.7% vs 17.0% base
    t = int(fnum(x.get('fbt')))
    if 0 < t <= 93500:
        s += 3
    elif 93500 < t <= 100000:
        s += 1
    else:
        s -= 1

    # turnover: <5% -> 23.5-26.0%, >20% -> 5.7%
    hs = fnum(x.get('hs'))
    if hs < 5:
        s += 3
    elif hs < 10:
        s += 1
    elif hs < 20:
        s -= 2
    else:
        s -= 3
    if hs > 10:
        flags.append('VOL>10%')

    # seal order / float mcap: >4% -> 40.0%, <1% -> 13.4%
    ltsz = fnum(x.get('ltsz')) / 1e8
    fund = fnum(x.get('fund')) / 1e8
    seal = (fund / ltsz * 100) if ltsz else 0.0
    if seal > 4:
        s += 4
    elif seal >= 2:
        s += 3
    elif seal >= 1:
        s += 1
    else:
        s -= 1
    if seal < 1:
        flags.append('SEAL<1%')

    # float mcap: 20-50 yi -> 21.5%, <20 yi -> 10.0%
    if 20 <= ltsz <= 50:
        s += 2
    elif 50 < ltsz <= 100:
        s += 0
    else:
        s -= 1

    # times the board broke today: 0 -> 18.7%, 2+ -> 10.5%
    zbc = int(fnum(x.get('zbc')))
    if zbc == 0:
        s += 1
    elif zbc >= 2:
        s -= 2
    if zbc >= 1:
        flags.append('BROKE%d' % zbc)

    # sector effect: >=5 same-sector limit-ups -> 21.5% vs isolated 15.3%
    if sector_count >= 5:
        s += 2
    elif sector_count >= 3:
        s += 1

    if s >= 9:
        grade = 'A'
    elif s >= 7:
        grade = 'B'
    elif s >= 4:
        grade = 'C'
    else:
        grade = 'D'

    # Seal strength (fund / float mcap) says "how strong"; seal order vs traded
    # value (fund / amount) says "how likely the next session opens at the
    # limit, i.e. how unlikely you are to get filled". They are correlated but
    # answer different questions -- see docs/03_forward_backtest_2026-10.md.
    amt = fnum(x.get('amount')) / 1e8
    seal_amt = (fund / amt * 100) if amt else 0.0

    return s, grade, ','.join(flags) or '-', round(seal_amt, 1)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def build_rows(zt):
    hy = Counter(x.get('hybk', '') for x in zt)
    rows = []
    for x in zt:
        sector = x.get('hybk', '') or ''
        sc, grade, flags, seal_amt = score(x, hy.get(sector, 1))
        ltsz = fnum(x.get('ltsz')) / 1e8
        seal = (fnum(x.get('fund')) / 1e8 / ltsz * 100) if ltsz else 0.0
        rows.append({
            'code': str(x.get('c', '')),
            'name': str(x.get('n', '')),
            'boards': int(fnum(x.get('lbc', 1))),
            'seal_time': hhmm(fnum(x.get('fbt'))),
            'turnover': round(fnum(x.get('hs')), 2),
            'seal_pct': round(seal, 2),
            'seal_amt_pct': seal_amt,
            'float_yi': round(ltsz, 1),
            'broke': int(fnum(x.get('zbc'))),
            'sector': sector,
            'sector_n': hy.get(sector, 1),
            'score': sc,
            'grade': grade,
            'flags': flags,
        })
    rows.sort(key=lambda r: -r['score'])
    return rows


def print_table(rows, top):
    hdr = ('%-4s %-6s %-4s %-7s %-7s %-7s %-7s %-9s %-4s %-14s %-3s %-7s %s'
           % ('GRD', 'SCORE', 'BRD', 'SEAL@', 'TURN%', 'SEAL%', 'SAMT%',
              'FLOAT', 'BRK', 'SECTOR', 'N', 'CODE', 'NAME'))
    print(hdr)
    print('-' * 108)
    for r in rows[:top]:
        print('%-4s %-6s %-4d %-7s %-7.1f %-7.2f %-7.1f %-9s %-4d %s %-3d %-7s %s'
              % (r['grade'], '%+d' % r['score'], r['boards'], r['seal_time'],
                 r['turnover'], r['seal_pct'], r['seal_amt_pct'],
                 '%.1fYi' % r['float_yi'], r['broke'], fit(r['sector'], 14),
                 r['sector_n'], r['code'], r['name']))
        if r['flags'] != '-':
            print('      flags: %s' % r['flags'])


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Scan the Eastmoney limit-up pool and grade A-share '
                    'yaogu candidates against measured base rates.')
    ap.add_argument('date', nargs='?',
                    help='trading day as YYYYMMDD (default: today)')
    ap.add_argument('--top', type=int, default=45,
                    help='rows to print in the main table (default 45)')
    ap.add_argument('--min-score', type=int, default=7,
                    help='minimum score for next-day candidates (default 7)')
    ap.add_argument('--json', dest='json_out', metavar='PATH',
                    help='also write the full result as JSON')
    args = ap.parse_args(argv)

    date = args.date or datetime.datetime.now().strftime('%Y%m%d')
    zt = pool('getTopicZTPool', date)
    zb = pool('getTopicZBPool', date)
    dt = pool('getTopicDTPool', date)
    if not zt:
        raise SystemExit('No limit-up pool for %s '
                         '(weekend / holiday, or older than the ~1 month '
                         'retention window of the Eastmoney pool API).' % date)

    broke_rate = len(zb) / float(len(zt) + len(zb)) * 100 if (zt or zb) else 0.0
    print('=' * 108)
    print('DATE %s | limit-up %d | broken %d | broke-rate %.1f%% | limit-down %d'
          % (date, len(zt), len(zb), broke_rate, len(dt)))
    print(BENCH)
    print('=' * 108)

    rows = build_rows(zt)
    print_table(rows, args.top)

    cand = [r for r in rows if r['boards'] == 1 and r['score'] >= args.min_score]
    print('\n>>> NEXT-DAY CANDIDATES (first board, score >= %d): %d'
          % (args.min_score, len(cand)))
    for r in cand:
        print('    %s %s  score %+d  seal@%s  turn %.1f%%  float %.1fYi  '
              'sector %s(%d)  %s'
              % (r['code'], r['name'], r['score'], r['seal_time'],
                 r['turnover'], r['float_yi'], r['sector'], r['sector_n'],
                 r['flags']))

    warn = [r for r in rows if r['boards'] >= 2 and
            (r['seal_pct'] < 1 or r['turnover'] > 10 or r['broke'] >= 1)]
    print('\n>>> EXIT WARNINGS (>=2 boards and thin seal / high turnover / '
          'broke the board): %d' % len(warn))
    for r in warn:
        print('    %s %s  %d-boards  seal %.2f%%  turn %.1f%%  broke %d  -> %s'
              % (r['code'], r['name'], r['boards'], r['seal_pct'],
                 r['turnover'], r['broke'], r['flags']))

    print('\nBroken-board names today (next-day re-limit rate only %.1f%%) '
          '- do NOT wait for a re-seal:' % BROKE_RELIMIT)
    for x in zb[:12]:
        print('    %s %s  broke %dx  turn %.1f%%  %s'
              % (x.get('c', ''), x.get('n', ''), int(fnum(x.get('zbc'))),
                 fnum(x.get('hs')), x.get('hybk', '')))

    if args.json_out:
        payload = {
            'date': date,
            'source': 'eastmoney push2ex',
            'base_rates': {'first_board': 17.0, 'boards_2': 36.1,
                           'boards_3': 40.6, 'boards_4': 42.9, 'boards_5p': 50.0},
            'market': {'limit_up': len(zt), 'broken': len(zb),
                       'broke_rate': round(broke_rate, 1),
                       'limit_down': len(dt)},
            'rows': rows,
            'next_day_candidates': [r['code'] for r in cand],
            'exit_warnings': [r['code'] for r in warn],
            'broken_today': [str(x.get('c', '')) for x in zb],
        }
        out_dir = os.path.dirname(os.path.abspath(args.json_out))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.json_out, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        print('\nJSON written to %s' % args.json_out)

    print('\nNOTE: base rates come from 14 trading days of a single market '
          'regime. Treat them as directional odds, not win rates.')


if __name__ == '__main__':
    main()
