#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Limit-down pool scanner for A-share names that closed at the down limit.

Built to answer the question the limit-up scanner cannot: *is a limit-down
bounce tradeable at all?* The short answer from the backtest is no, except on
panic days -- see docs/04_reverse_backtest_2026-10.md.

Two things this script does that the original skill card said were impossible:

  1. It reads the limit-down pool successfully. getTopicDTPool only answers to
     ``sort=fund:asc``; reusing the limit-up sort (``fbt:asc``) returns
     ``{"tc": N, "pool": []}`` -- a total with an empty pool, which looks like a
     dead endpoint. It is not.
  2. It grades each name by *why* it is limit-down, which is what decides
     whether a bounce is a mean-reversion trade (panic day) or a falling knife
     (company-specific bad news).

Usage:
    python3 scripts/scan_dt_pool.py                 # today
    python3 scripts/scan_dt_pool.py 20261021        # a specific trading day
    python3 scripts/scan_dt_pool.py 20261021 --backtest   # + base-rate table

Standard library only.
"""
import argparse
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaogu_data as YD  # noqa: E402
import yaogu_fetch as YF  # noqa: E402
from scan_zt_pool import pool, hhmm  # noqa: E402

RISK_KW = ('风险提示', '异常波动', '停牌', '核查', '严重异常', '关注函',
           '监管工作函', '警示')
# Sponsor / legal-opinion boilerplate contains 核查 and 保荐 but is not a risk
# signal -- it is the noise that makes keyword matching useless if left in.
NOISE_KW = ('保荐', '持续督导', '核查意见', '上市保荐', '验资', '法律意见',
            '审计报告')


def kline(code, lmt=60):
    return YF.kline(code, lmt)


def notices(code, limit=40):
    return YF.notices(code, limit)


def warn_before(code, date):
    """Risk warnings published *before* the limit-down day (exit signal 5)."""
    hits = []
    for d, t in notices(code):
        if d.replace('-', '') >= date:
            continue
        if any(k in t for k in NOISE_KW):
            continue
        if any(k in t for k in RISK_KW):
            hits.append('%s %s' % (d, t[:26]))
    return hits


def tags(x, zt_days, kls, date):
    code = x['c']
    amount = x.get('amount') or 1
    ltsz = (x.get('ltsz') or 0) / 1e8
    hist = sorted(zt_days.get(code, set()))
    pre20 = boll = None
    ks = sorted(kls)
    if date in kls:
        i = ks.index(date)
        if i >= 20:
            closes = [float(kls[k][2]) for k in ks[:i + 1]]
            base = closes[-21]
            pre20 = (closes[-1] / base - 1) * 100 if base > 0 else None
            w = closes[-20:]
            ma = sum(w) / 20
            sd = statistics.pstdev(w) or 1e-9
            lo, up = ma - 2 * sd, ma + 2 * sd
            boll = (closes[-1] - lo) / (up - lo) if up > lo else None
    return dict(code=code, name=x['n'], price=(x.get('p') or 0) / 1000.0,
                zdp=x.get('zdp'), hs=x.get('hs') or 0,
                fb=(x.get('fund') or 0) / amount, ltsz=ltsz,
                blood=any(d < date for d in hist),
                pre20=pre20, boll=boll,
                lbt=str(x.get('lbt') or '').zfill(6), sector=x.get('hybk'))


def grade(r, warn):
    """A limit-down name has no 'buy grade', only 'can I trust the bounce'."""
    pts = []
    if warn:
        pts.append('已发风险提示/异动公告')
    if r['fb'] >= 0.15:
        pts.append('封死(封单/成交%.0f%%)' % (r['fb'] * 100))
    elif r['fb'] < 0.05:
        pts.append('弱封(%.0f%%，有撬板)' % (r['fb'] * 100))
    if r['blood']:
        pts.append('有涨停血统(10日内)')
    if (r['pre20'] or 0) >= 30:
        pts.append('前20日涨%.0f%%(高位)' % r['pre20'])
    if r['hs'] >= 15:
        pts.append('换手%.0f%%(放量)' % r['hs'])
    lvl = 'X' if (warn or r['fb'] >= 0.15) else ('D' if r['blood'] else 'C')
    return lvl, pts


def scan(date, run_backtest=False):
    cache = YD.read_json('zt_study.json', {}) or {}
    zt_days = {}
    for k, v in cache.items():
        for x in (v.get('zt') or []):
            zt_days.setdefault(x['c'], set()).add(k)

    dt = pool('getTopicDTPool', date)
    n_zt = len(pool('getTopicZTPool', date))
    print('=== %s  limit-down %d | limit-up %d ===' % (date, len(dt), n_zt))
    if not dt:
        print('(no name closed at the down limit)')
        return []

    rows = []
    for x in dt:
        r = tags(x, zt_days, kline(x['c']), date)
        w = warn_before(x['c'], date)
        lvl, pts = grade(r, w)
        r.update(warn=w, level=lvl, why=pts)
        rows.append(r)

    rows.sort(key=lambda r: ({'X': 0, 'D': 1, 'C': 2}[r['level']], -r['fb']))
    for r in rows:
        print('\n[%s] %s %s  %+.2f%%  price %.2f  limit-down at %s'
              % (r['level'], r['code'], r['name'], r['zdp'] or 0, r['price'],
                 hhmm(r['lbt'])))
        print('    turn %.1f%%  seal/amount %.1f%%  float %.0fYi  '
              'pre20 %s  %%b=%s  sector %s'
              % (r['hs'], r['fb'] * 100, r['ltsz'],
                 ('%+.1f%%' % r['pre20']) if r['pre20'] is not None else 'N/A',
                 ('%.2f' % r['boll']) if r['boll'] is not None else 'N/A',
                 r['sector']))
        print('    tags: ' + ' | '.join(r['why']))
        for h in r['warn'][:3]:
            print('    notice: ' + h)

    print('\nlevel X = unbuyable (sealed) or already flagged by the company')
    print('      D = had limit-up blood, C = no limit-up record')
    print('NOTE: a bounce trade only has statistical support on panic days '
          '(small-cap index down >1%% that day). See '
          'docs/04_reverse_backtest_2026-10.md.')

    if run_backtest:
        base_rates()
    return rows


def base_rates():
    """Limit-down base rates from the cached pools (no network if cached)."""
    zt = YD.read_json('zt_study.json', {}) or {}
    days = [k for k in sorted(zt) if zt[k].get('zt')]
    if not days:
        print('missing zt_study.json, cannot recompute')
        return
    dtp = YD.read_json('dt_pools.json', {}) or {}
    fetched = 0
    for d in days:
        if d not in dtp:
            dtp[d] = pool('getTopicDTPool', d)
            fetched += 1
            time.sleep(0.5)
    if fetched:
        print('  (fetched %d missing limit-down days -> %s)'
              % (fetched, YD.write_json('dt_pools.json', dtp)))

    store = YD.read_json('kl_store.json', {}) or {}
    codes = sorted({x['c'] for v in dtp.values() for x in v})
    added = 0
    for c in codes:
        if c not in store:
            store[c] = [list(v) for v in kline(c).values()]
            added += 1
            time.sleep(0.22)
    if added:
        print('  (fetched %d missing K-lines -> %s)'
              % (added, YD.write_json('kl_store.json', store)))

    zt_days = {}
    for k in days:
        for x in (zt[k].get('zt') or []):
            zt_days.setdefault(x['c'], set()).add(k)

    def series(code):
        m = {r[0].replace('-', ''): float(r[2]) for r in store.get(code, [])}
        return m, sorted(m)

    obs = []
    for i, d in enumerate(days[:-1]):
        nd = days[i + 1]
        for x in dtp.get(d, []):
            m, ks = series(x['c'])
            if d not in ks or nd not in ks:
                continue
            base = m[d]
            if base <= 0:
                continue
            j = ks.index(d)
            path = [m[ks[j + k]] / base * 100 - 100
                    for k in range(1, 6) if j + k < len(ks)]
            obs.append(dict(t1=m[nd] / base * 100 - 100,
                            hs=x.get('hs') or 0,
                            fb=(x.get('fund') or 0) / (x.get('amount') or 1),
                            blood=any(z < d for z in zt_days.get(x['c'], set())),
                            path=path))

    def show(rows, label):
        v = [r['t1'] for r in rows]
        if not v:
            print('  %-26s n=0' % label)
            return
        v3 = [r['path'][2] for r in rows if len(r['path']) >= 3]
        print('  %-26s n=%-4d T+1 mean %+6.2f%% median %+6.2f%% up %3.0f%%'
              ' | T+3 median %+6.2f%%'
              % (label, len(v), sum(v) / len(v), statistics.median(v),
                 sum(1 for x in v if x > 0) / len(v) * 100,
                 statistics.median(v3) if v3 else 0))

    print('\n=== limit-down base rates (%d observations) ===' % len(obs))
    show(obs, 'all limit-down names')
    show([r for r in obs if r['blood']], 'had limit-up blood')
    show([r for r in obs if not r['blood']], 'no limit-up record')
    show([r for r in obs if r['fb'] < 0.05], 'weak seal (<5%)')
    show([r for r in obs if r['fb'] >= 0.15], 'sealed (>=15%)')
    show([r for r in obs if r['hs'] >= 15], 'turnover >=15%')


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Scan the Eastmoney limit-down pool and grade whether a '
                    'bounce is worth waiting for.')
    ap.add_argument('date', nargs='?', default=time.strftime('%Y%m%d'),
                    help='trading day as YYYYMMDD (default: today)')
    ap.add_argument('--backtest', action='store_true',
                    help='also recompute the limit-down base-rate table')
    args = ap.parse_args(argv)
    scan(args.date, run_backtest=args.backtest)


if __name__ == '__main__':
    main()
