#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reproduce the base-rate tables in docs/01_base_rate_study_2026-10.md.

Fetches the Eastmoney limit-up / broken-board / limit-down pools for every
trading day in a window, walks day -> next day, and reports continuation rates
broken down by board count, seal time, turnover, seal order, float mcap and
same-sector breadth.

Usage:
    python3 scripts/base_rates.py                          # default window
    python3 scripts/base_rates.py 20260901 20261113        # explicit window
    python3 scripts/base_rates.py 20260923 20261020 --offline   # cache only
    python3 scripts/base_rates.py 20260901 20261113 --refetch   # ignore cache

Cache resolution (first hit wins for writes, all hits are merged for reads):
    1. --cache PATH
    2. $YAOGU_CACHE
    3. ~/.cache/yaogu/zt_study.json
    4. data/zt_study.json shipped in this repo (read-only seed)

The seed is never overwritten: the historical window it contains is already
outside the ~1 month retention of the Eastmoney pool API, so it cannot be
re-fetched. New days accumulate on top of it in the user cache.

Standard library only, no third-party dependencies.
"""
import argparse
import json
import os
import statistics
import sys
import time
import urllib.request
from collections import Counter, defaultdict

UA = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'}
UT = '7eea3edcaed734bea9cbfc24409ed989'
POOL_URL = ('https://push2ex.eastmoney.com/%s?ut=%s&dpt=wz.ztzt'
            '&Pageindex=0&pagesize=400&sort=%s&date=%s')
# The limit-down pool only answers to sort=fund:asc. Reusing the limit-up sort
# (fbt:asc) returns {"tc": N, "pool": []} -- a total with an empty pool, which
# looks like a dead endpoint but is just the wrong sort key. This is why the
# first version of this study reported "limit-down pool is always empty".
SORTS = {'getTopicZTPool': 'fbt%3Aasc', 'getTopicZBPool': 'fbt%3Aasc',
         'getTopicDTPool': 'fund%3Aasc'}
KLINE_URL = ('https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000001'
             '&fields1=f1,f2,f3&fields2=f51,f52,f53&klt=101&fqt=1'
             '&beg=%s&end=%s')

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED_PATH = os.path.join(REPO_ROOT, 'data', 'zt_study.json')
CACHE_DIR = (os.environ.get('YAOGU_CACHE_DIR')
             or os.path.join(os.path.expanduser('~'), '.cache', 'yaogu'))
DEFAULT_CACHE = os.path.join(CACHE_DIR, 'zt_study.json')

LABELS = {1: 'first-board', 2: '2-board', 3: '3-board', 4: '4-board',
          5: '5-board+'}

# Days whose pool was captured mid-session instead of at the close. They are
# kept in the seed for provenance but must never enter the statistics, otherwise
# a half-finished pool is scored against a complete next day.
INTRADAY_SNAPSHOT_DAYS = {'20261021'}


# --------------------------------------------------------------------------- #
# data access
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
    raise SystemExit('fetch failed: %s (%s)' % (url[:80], last))


def trading_days(beg, end):
    kl = jget(KLINE_URL % (beg, end))['data']['klines']
    return [k.split(',')[0].replace('-', '') for k in kl]


def fetch(dates):
    apis = (('zt', 'getTopicZTPool'), ('zb', 'getTopicZBPool'),
            ('dt', 'getTopicDTPool'))
    out = {}
    for d in dates:
        rec = {}
        for tag, api in apis:
            rec[tag] = ((jget(POOL_URL % (api, UT, SORTS[api], d)).get('data')
                         or {}).get('pool') or [])
        out[d] = rec
        print('  fetched %s  zt=%-3d zb=%-3d dt=%-3d'
              % (d, len(rec['zt']), len(rec['zb']), len(rec['dt'])), flush=True)
    return out


def load_cache(cache_path):
    """Merge the shipped seed with the user cache (user cache wins per day)."""
    merged = {}
    for path, tag in ((SEED_PATH, 'seed'), (cache_path, 'cache')):
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding='utf-8') as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            print('WARN: cannot read %s (%s), skipped' % (path, exc))
            continue
        n_valid = sum(1 for v in data.values()
                      if isinstance(v, dict) and (v.get('zt') or v.get('zb')))
        merged.update(data)
        print('  loaded %-5s %s  (%d days, %d with data)'
              % (tag, path, len(data), n_valid))
    return merged


def save_cache(data, cache_path):
    parent = os.path.dirname(os.path.abspath(cache_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(cache_path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False)
    print('  saved cache -> %s' % cache_path)


# --------------------------------------------------------------------------- #
# statistics
# --------------------------------------------------------------------------- #
def run(D, beg, end, end_exclusive=None):
    inwin = [d for d in sorted(D) if beg <= d <= end]
    valid = [d for d in inwin if D[d].get('zt') or D[d].get('zb')]
    empty = [d for d in inwin if not (D[d].get('zt') or D[d].get('zb'))]
    today = time.strftime('%Y%m%d')
    # today's pool is a mid-session snapshot until the close; never count it
    valid = [d for d in valid if d != today]
    intraday = [d for d in valid if d in INTRADAY_SNAPSHOT_DAYS]
    if intraday:
        print('  dropping known intraday snapshot day(s): %s'
              % ', '.join(intraday))
    valid = [d for d in valid if d not in INTRADAY_SNAPSHOT_DAYS]
    if end_exclusive:
        valid = [d for d in valid if d != end_exclusive]
    if len(valid) < 3:
        raise SystemExit('not enough valid days (%d); the Eastmoney pool API '
                         'only reaches back ~1 month' % len(valid))

    def codes(day, tag='zt'):
        return {str(x.get('c')): x for x in D[day].get(tag) or []}

    print('\nVALID WINDOW %d days: %s -> %s   (dropped %d empty days)'
          % (len(valid), valid[0], valid[-1], len(empty)))

    ztn = [len(D[d].get('zt') or []) for d in valid]
    zbr = [len(D[d].get('zb') or []) /
           float(len(D[d].get('zt') or []) + len(D[d].get('zb') or [])) * 100
           for d in valid
           if len(D[d].get('zt') or []) + len(D[d].get('zb') or [])]
    print('MARKET TEMP: limit-ups mean %.1f median %d (%d..%d) | '
          'broke-rate mean %.1f%% median %.1f%%'
          % (statistics.mean(ztn), statistics.median(ztn), min(ztn), max(ztn),
             statistics.mean(zbr), statistics.median(zbr)))

    trans = defaultdict(lambda: [0, 0])
    out = defaultdict(Counter)
    for i, d in enumerate(valid[:-1]):
        nzt = codes(valid[i + 1])
        nzb = codes(valid[i + 1], 'zb')
        for c, x in codes(d).items():
            k = int(float(x.get('lbc', 1) or 1))
            kk = k if k <= 4 else 5
            trans[kk][1] += 1
            if c in nzt:
                trans[kk][0] += 1
                out[kk]['zt'] += 1
            elif c in nzb:
                out[kk]['zb'] += 1
            else:
                out[kk]['flat'] += 1

    print('\nTABLE 1.1  next-day continuation by board count')
    print('  %-12s %6s %10s %10s %10s' % ('state', 'n', 'cont%', 'broke%',
                                          'flat%'))
    for k in sorted(trans):
        c, t = trans[k]
        print('  %-12s %6d %9.1f%% %9.1f%% %9.1f%%'
              % (LABELS[k], t, c / t * 100.0, out[k]['zb'] / t * 100.0,
                 out[k]['flat'] / t * 100.0))

    def collect(mode):
        bag = []
        for i, d in enumerate(valid[:-1]):
            nzt = codes(valid[i + 1])
            for c, x in codes(d).items():
                lbc = int(float(x.get('lbc', 1) or 1))
                if mode == 'first' and lbc != 1:
                    continue
                if mode == 'high' and lbc < 2:
                    continue
                ltsz = float(x.get('ltsz') or 0) / 1e8
                bag.append({
                    'fbt': int(float(x.get('fbt') or 0)),
                    'zbc': int(float(x.get('zbc') or 0)),
                    'ltsz': ltsz,
                    'hs': float(x.get('hs') or 0),
                    'seal': (float(x.get('fund') or 0) / 1e8 / ltsz * 100)
                            if ltsz else 0.0,
                    'cont': 1 if c in nzt else 0,
                    'hybk': x.get('hybk', ''),
                })
        return bag

    def rep(title, key, edges, labels, bag):
        base = sum(x['cont'] for x in bag) / float(len(bag)) * 100
        groups = defaultdict(lambda: [0, 0])
        for x in bag:
            b = len(edges)
            for i, e in enumerate(edges):
                if x[key] < e:
                    b = i
                    break
            groups[b][1] += 1
            groups[b][0] += x['cont']
        print('  -- %s (base %.1f%%)' % (title, base))
        for b in sorted(groups):
            c, t = groups[b]
            rate = c / t * 100.0 if t else 0.0
            print('     %-16s n=%4d cont=%5.1f%% (%+.1fpt)'
                  % (labels[b], t, rate, rate - base if t else 0))

    first = collect('first')
    base = sum(x['cont'] for x in first) / float(len(first)) * 100
    print('\nTABLE 1.2  first-board n=%d, base continuation %.1f%%'
          % (len(first), base))
    rep('seal time', 'fbt', [93500, 100000, 103000, 130000, 999999],
        ['0925-0935', '0935-1000', '1000-1030', '1030-1300', 'after1300'],
        first)
    rep('turnover', 'hs', [3, 5, 10, 20, 999],
        ['<3%', '3-5%', '5-10%', '10-20%', '>20%'], first)
    rep('float mcap(Yi)', 'ltsz', [20, 30, 50, 100, 9999],
        ['<20', '20-30', '30-50', '50-100', '>100'], first)
    rep('broke count', 'zbc', [1, 2, 99], ['0', '1', '2+'], first)
    rep('seal/float', 'seal', [1, 2, 4, 999], ['<1%', '1-2%', '2-4%', '>4%'],
        first)

    hi = collect('high')
    print('\nTABLE 1.3  high-position (>=2 boards) n=%d' % len(hi))
    rep('turnover', 'hs', [5, 10, 20, 999], ['<5%', '5-10%', '10-20%', '>20%'],
        hi)
    rep('seal/float', 'seal', [1, 2, 4, 999], ['<1%', '1-2%', '2-4%', '>4%'],
        hi)
    rep('broke count', 'zbc', [1, 2, 99], ['0', '1', '2+'], hi)

    zb_next = [1 if c in codes(valid[i + 1]) else 0
               for i, d in enumerate(valid[:-1])
               for c in codes(d, 'zb')]
    print('\nTABLE 1.4  names that BROKE today: n=%d, next-day re-limit %.1f%%'
          % (len(zb_next), sum(zb_next) / float(len(zb_next)) * 100))

    # TABLE 1.5 -- tail risk. Needs the limit-down pool, which only exists for
    # days fetched with sort=fund:asc (the shipped seed predates that fix).
    print('\nTABLE 1.5  tail risk: today limit-up -> tomorrow limit-down')
    pairs = [(d, valid[i + 1]) for i, d in enumerate(valid[:-1])
             if D[valid[i + 1]].get('dt')]
    if not pairs:
        print('  no limit-down pool in cache for the next-day legs '
              '(run online, or the days were fetched with the wrong sort key)')
    else:
        zt_hit = [1 if c in codes(nd, 'dt') else 0
                  for d, nd in pairs for c in codes(d)]
        zb_hit = [1 if c in codes(nd, 'dt') else 0
                  for d, nd in pairs for c in codes(d, 'zb')]
        for lab, bag in (('all limit-up names', zt_hit),
                         ('names that broke today', zb_hit)):
            if bag:
                print('  %-26s n=%4d  next-day limit-down %5.1f%%'
                      % (lab, len(bag), sum(bag) / float(len(bag)) * 100))

    print('\nNOTE: short window and single market regime; overlapping streaks '
          'inflate the nominal n. Treat these as directional odds, NOT win '
          'rates. For tradability (can you actually get filled at the limit?) '
          'see docs/03_forward_backtest_2026-10.md -- base rates alone '
          'overstate what a real order can capture.')


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Recompute the yaogu base-rate tables from Eastmoney pools.')
    ap.add_argument('beg', nargs='?', default='20260901',
                    help='window start, YYYYMMDD (default 20260901)')
    ap.add_argument('end', nargs='?', default='20991231',
                    help='window end, YYYYMMDD (default: up to latest)')
    ap.add_argument('--offline', action='store_true',
                    help='do not hit the network; use cached days only')
    ap.add_argument('--refetch', action='store_true',
                    help='re-fetch days that are already cached')
    ap.add_argument('--cache', metavar='PATH',
                    help='cache file (default $YAOGU_CACHE or '
                         '~/.cache/yaogu/zt_study.json)')
    ap.add_argument('--exclude', metavar='YYYYMMDD',
                    help='drop one more day, e.g. an intraday snapshot')
    args = ap.parse_args(argv)

    cache_path = (args.cache or os.environ.get('YAOGU_CACHE')
                  or DEFAULT_CACHE)
    print('cache target: %s' % cache_path)
    D = load_cache(cache_path)

    if not args.offline:
        days = trading_days(args.beg, args.end)
        today = time.strftime('%Y%m%d')
        todo = [d for d in days if args.refetch or d not in D]
        if today in days and today not in todo:
            todo.append(today)  # refresh today's intraday snapshot
        if todo:
            print('fetching %d trading day(s): %s .. %s'
                  % (len(todo), todo[0], todo[-1]))
            D.update(fetch(todo))
            save_cache(D, cache_path)
        else:
            print('cache already covers the window; nothing to fetch')

    run(D, args.beg, args.end, end_exclusive=args.exclude)


if __name__ == '__main__':
    main()
