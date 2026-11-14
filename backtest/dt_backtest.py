#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reverse backtest: is "buying the down limit" tradeable at all?

Same discipline as the forward backtest -- real open prices to split the
overnight gap from the intraday leg, explicit costs, a small-cap index
benchmark, and cost sensitivity because the whole result hinges on it.

Four executable paths (names sealed at the down limit cannot be bought):
    S1  buy at the down limit (close) -> sell next open
    S2  buy at the down limit (close) -> sell next close
    S3  buy next open                 -> sell next close
    S4  buy next open                 -> sell T+2 close

Data
    data/dt_pools.json   limit-down pool snapshots
    data/kl_store.json   daily K-lines (open/close/turnover) for those codes
    data/bench.json      CSI 2000 daily closes
    data/bench_oc.json   CSI 2000 daily open/close (for intraday excess)

Usage:
    python3 backtest/dt_backtest.py
    python3 backtest/dt_backtest.py --cost 0.001 --slip 0.001 --maxfb 0.05

Result: S1 wins 4.2% of the time. Only S3 (next open -> same-day close) is
positive, and only on panic days (index down >1% that session) does it survive
a realistic cost. See docs/04_reverse_backtest_2026-10.md.

Standard library only.
"""
import argparse
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))

import yaogu_data as YD  # noqa: E402

PANIC_DROP = -1.0  # index close-to-close, in percent


def stat(rows, key, cost, label, bench_key=None):
    v = [r[key] for r in rows if r.get(key) is not None]
    if not v:
        print('%-30s n=0' % label)
        return None
    net = [x - cost * 100 for x in v]
    win = sum(1 for x in net if x > 0) / len(net) * 100
    gains = [x for x in net if x > 0]
    losses = [x for x in net if x <= 0]
    pf = (sum(gains) / abs(sum(losses))) if losses and sum(losses) != 0 else float('inf')
    line = ('%-30s n=%-3d win%5.1f%% mean%+6.2f%% med%+6.2f%% pf%5.2f '
            'best%+6.1f%% worst%+6.1f%%'
            % (label, len(net), win, sum(net) / len(net),
               statistics.median(net), pf, max(net), min(net)))
    if bench_key:
        ex = [r[key] - r[bench_key] - cost * 100 for r in rows
              if r.get(key) is not None and r.get(bench_key) is not None]
        if ex:
            line += (' | excess win%5.1f%% excess mean%+6.2f%%'
                     % (sum(1 for x in ex if x > 0) / len(ex) * 100,
                        sum(ex) / len(ex)))
    print(line)
    return dict(n=len(net), win=win, mean=sum(net) / len(net),
                med=statistics.median(net), pf=pf, worst=min(net))


def tday(rows, key, cost, label, bench_key=None):
    """t-test on daily aggregated returns (the only honest sample size here)."""
    byd = {}
    for r in rows:
        if r.get(key) is None:
            continue
        val = r[key] - cost * 100
        if bench_key and r.get(bench_key) is not None:
            val = r[key] - r[bench_key] - cost * 100
        byd.setdefault(r['d'], []).append(val)
    if len(byd) < 3:
        return
    s = [sum(v) / len(v) for v in byd.values()]
    m = sum(s) / len(s)
    sd = statistics.pstdev(s)
    t = m / (sd / math.sqrt(len(s))) if sd else 0
    print('   [daily agg] %-28s %d days  mean%+.3f%%/day  sd%.2f%%  t=%5.2f'
          '  positive %d/%d'
          % (label, len(s), m, sd, t, sum(1 for x in s if x > 0), len(s)))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Backtest limit-down bounce strategies on real prices with '
                    'costs and an index benchmark.')
    ap.add_argument('--cost', type=float, default=0.0010,
                    help='commission+tax+transfer as a fraction, default 0.0010')
    ap.add_argument('--slip', type=float, default=0.0010,
                    help='one-way slippage estimate, default 0.0010')
    ap.add_argument('--maxfb', type=float, default=0.05,
                    help='buyable if seal order / traded value < this '
                         '(default 0.05)')
    args = ap.parse_args(argv)
    total_cost = args.cost + args.slip

    dtp = YD.read_fixture('dt_pools.json')
    kl = YD.read_fixture('kl_store.json')
    bench = YD.read_fixture('bench.json')
    boc = YD.read_fixture('bench_oc.json')
    if not dtp or not kl or not bench or not boc:
        raise SystemExit('missing data: need dt_pools.json, kl_store.json, '
                         'bench.json and bench_oc.json in data/ '
                         '(see docs/02_usage_and_data.md)')

    days = [k for k in sorted(dtp) if dtp[k]]
    print('window %s ~ %s, %d limit-down days' % (days[0], days[-1], len(days)))
    print('cost: commission+tax %.2f%% + slippage %.2f%% = %.2f%% round trip\n'
          % (args.cost * 100, args.slip * 100, total_cost * 100))

    def kr(code):
        return {r[0].replace('-', ''): r for r in kl.get(code, [])}

    idx = boc['国证2000']          # date -> [open, close]
    idx_close = bench['国证2000']  # date -> close
    # index move on the limit-down day itself, used to label panic sessions
    mkt = {}
    for i, d in enumerate(days):
        if i == 0:
            continue
        prev = idx_close.get(days[i - 1])
        cur = idx_close.get(d)
        if prev and cur:
            mkt[d] = (cur / prev - 1) * 100

    obs = []
    for i, d in enumerate(days):
        if i + 1 >= len(days):
            continue
        nd = days[i + 1]
        b0, b1 = idx_close.get(d), idx_close.get(nd)
        o1, c1 = idx.get(nd) if idx.get(nd) else (None, None)
        for x in dtp[d]:
            m = kr(x['c'])
            if d not in m or nd not in m:
                continue
            r0, r1 = m[d], m[nd]
            C0, O1, C1 = float(r0[2]), float(r1[1]), float(r1[2])
            if C0 <= 0 or O1 <= 0 or C1 <= 0:
                continue
            amt = x.get('amount') or 1
            rec = dict(d=d, nd=nd, c=x['c'], n=x['n'],
                       fb=(x.get('fund') or 0) / amt, hs=x.get('hs') or 0,
                       C0=C0, O1=O1, C1=C1,
                       gap=(O1 / C0 - 1) * 100,
                       day1=(C1 / O1 - 1) * 100,
                       hold1=(C1 / C0 - 1) * 100,
                       amt=amt / 1e8,
                       mkt=mkt.get(d))
            rec['bench_hold1'] = ((b1 / b0 - 1) * 100) if (b0 and b1) else None
            rec['bench_day1'] = ((c1 / o1 - 1) * 100) if (o1 and c1) else None
            if i + 2 < len(days):
                d2 = days[i + 2]
                if d2 in m:
                    rec['hold2'] = (float(m[d2][2]) / O1 - 1) * 100
                    b2 = idx_close.get(d2)
                    rec['bench_hold2'] = ((b2 / b1 - 1) * 100) if (b1 and b2) else None
            obs.append(rec)

    print('sample: %d stock-days\n' % len(obs))

    buyable = [r for r in obs if r['fb'] < args.maxfb]
    print('[0] Gate 1 -- can you buy it at all (queueing at the down limit)')
    sealed = [r for r in obs if r['fb'] >= 0.15]
    print('   sealed (seal/amount >= 15%%): %d (%.0f%%) -- your order will not fill'
          % (len(sealed), len(sealed) / len(obs) * 100))
    print('   weak seal (<%.0f%%): %d (%.0f%%) -- the only buyable subset'
          % (args.maxfb * 100, len(buyable), len(buyable) / len(obs) * 100))
    print('   median traded value of the buyable subset: %.2fYi -> ~%.0f Wan per '
          'name at 1%% participation\n'
          % (statistics.median([r['amt'] for r in buyable]),
             statistics.median([r['amt'] for r in buyable]) * 100))

    print('[1] Four paths (cost %.2f%%). S1/S2 need a fill at the down limit, '
          'so only the buyable subset counts;' % (total_cost * 100))
    print('    S3/S4 buy at the next open, so all names count.')
    stat(buyable, 'gap', total_cost, '  S1 close->next open')
    stat(buyable, 'hold1', total_cost, '  S2 close->next close', 'bench_hold1')
    stat(obs, 'day1', total_cost, '  S3 next open->next close', 'bench_day1')
    stat(obs, 'hold2', total_cost, '  S4 next open->T+2 close', 'bench_hold2')

    print('\n[2] Control: all limit-down names, close->next close')
    stat(obs, 'hold1', total_cost, '  all, close->next close', 'bench_hold1')

    print('\n[3] By seal strength (close->next close)')
    for lo, hi, nm in [(0, 0.02, '<2% (almost no volume)'), (0.02, 0.05, '2-5%'),
                       (0.05, 0.15, '5-15% (half sealed)'),
                       (0.15, 9, '>=15% (sealed, unbuyable)')]:
        stat([r for r in obs if lo <= r['fb'] < hi], 'hold1', total_cost,
             '  ' + nm, 'bench_hold1')

    print('\n[4] By number of limit-down names that day (S3, next open->close)')
    cnt = {d: len(dtp[d]) for d in days}
    for lo, hi, nm in [(0, 8, '<=7 names (calm)'), (8, 15, '8-14 names'),
                       (15, 999, '>=15 names (panic)')]:
        stat([r for r in obs if lo <= cnt.get(r['d'], 0) < hi], 'day1',
             total_cost, '  ' + nm, 'bench_day1')

    print('\n[5] Gate 2 -- can you sell?')
    stuck_open = [r for r in buyable if r['gap'] <= -9.5]
    stuck_close = [r for r in obs if r['hold1'] <= -9.5]
    print('   buyable names opening at the down limit next day: %d / %d = %.1f%%'
          % (len(stuck_open), len(buyable), len(stuck_open) / len(buyable) * 100))
    print('   all names closing at the down limit next day: %d / %d = %.1f%%'
          % (len(stuck_close), len(obs), len(stuck_close) / len(obs) * 100))
    print('   next open <= -5%%: %.1f%% | next open > 0%%: %.1f%%'
          % (sum(1 for r in buyable if r['gap'] <= -5) / len(buyable) * 100,
             sum(1 for r in buyable if r['gap'] > 0) / len(buyable) * 100))

    print('\n[6] Cost sensitivity of S3 (excess over the index intraday)')
    for c in (0.0, 0.002, 0.004, 0.006, 0.008):
        tday(obs, 'day1', c, 'S3 at %.1f%% cost' % (c * 100), 'bench_day1')

    print('\n[7] Portfolio: equal-weight every buyable name each day (S2)')
    byday = {}
    for r in buyable:
        byday.setdefault(r['d'], []).append(r['hold1'] - total_cost * 100)
    nets = []
    for d in sorted(byday):
        v = byday[d]
        nets.append(sum(v) / len(v))
        bch = statistics.mean([r['bench_hold1'] for r in buyable
                               if r['d'] == d and r.get('bench_hold1') is not None]
                              or [0])
        print('   %s  n=%-2d portfolio%+6.2f%%  index(CSI2000)%+6.2f%%  excess%+6.2f%%'
              % (d, len(v), sum(v) / len(v), bch, sum(v) / len(v) - bch))
    print('   -> daily %+0.3f%% / day win %.0f%% / cumulative %+0.1f%% / worst day %+0.2f%%'
          % (sum(nets) / len(nets), sum(1 for x in nets if x > 0) / len(nets) * 100,
             sum(nets), min(nets)))

    print('\n[8] The only subset that survives costs: PANIC days '
          '(index down >%.0f%% that session)' % abs(PANIC_DROP))
    panic = [r for r in obs if (r['mkt'] or 0) <= PANIC_DROP]
    pd_days = sorted({r['d'] for r in panic})
    print('   %d sessions: %s' % (len(pd_days), ', '.join(pd_days)))
    print('   %d stock-days\n' % len(panic))
    for c in (0.0, 0.002, 0.004, 0.006):
        tday(panic, 'day1', c, 'panic S3 excess @%.1f%%' % (c * 100), 'bench_day1')
    stat(panic, 'day1', total_cost, '  panic S3 absolute', 'bench_day1')

    print('\n   By index move on the limit-down day (S3, absolute):')
    for lo, hi, nm in [(-99, -1.0, 'down >1% (panic)'),
                       (-1.0, 0.0, 'down 0-1%'),
                       (0.0, 99, 'up')]:
        stat([r for r in obs if lo <= (r['mkt'] or 0) < hi], 'day1',
             total_cost, '  ' + nm, 'bench_day1')

    print('\nNOTE: S3 only holds up on panic days; the full sample loses '
          'significance at 0.4%% cost, and 78%% of the raw gain comes from the '
          '22%% tail.\n      This is mean reversion on a liquidity premium, NOT '
          'a yaogu (momentum) setup.')


if __name__ == '__main__':
    main()
