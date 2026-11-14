#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Forward backtest: is "buying the limit-up board" tradeable at all?

Splits the return into the overnight gap and the intraday leg using real open
prices, charges costs, compares against the CSI 2000 small-cap index, and runs
the t-test on *daily aggregated* returns -- because 693 stock-days are only 14
independent observations.

Four executable paths
    P1  buy on the board (close)  -> sell next open
    P2  buy on the board (close)  -> sell next close
    P3  buy next open             -> sell next close
    P4  buy next open             -> sell T+2 close

Data (see docs/02_usage_and_data.md)
    data/zt_study.json   limit-up / broken-board pool snapshots
    data/zt_kl.json      daily K-lines (open + close) for those codes
    data/bench_oc.json   CSI 2000 daily open/close

Usage:
    python3 backtest/zt_backtest.py
    python3 backtest/zt_backtest.py --cost 0.002

Result: the edge and the fillability are the same variable. Assuming every
order fills, +1.44% per trade; restricted to what you can actually buy,
-0.06% per trade. See docs/03_forward_backtest_2026-10.md.

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


def hhmm(t):
    s = str(int(t or 0)).zfill(6)
    return s[:2] + ':' + s[2:4]


def stat(rows, key, cost, label, bench=None, quiet=False):
    v = [r[key] for r in rows if r.get(key) is not None]
    if not v:
        if not quiet:
            print('%-30s n=0' % label)
        return None
    net = [x - cost * 100 for x in v]
    gains = [x for x in net if x > 0]
    losses = [x for x in net if x <= 0]
    pf = (sum(gains) / abs(sum(losses))) if losses and sum(losses) != 0 else 99.0
    line = ('%-28s n=%-3d win%5.1f%% mean%+6.2f%% med%+6.2f%% pf%5.2f '
            'best%+6.1f%% worst%+6.1f%%'
            % (label, len(net), sum(1 for x in net if x > 0) / len(net) * 100,
               sum(net) / len(net), statistics.median(net), pf, max(net),
               min(net)))
    if bench:
        ex = [r[key] - r[bench] - cost * 100 for r in rows
              if r.get(key) is not None and r.get(bench) is not None]
        if ex:
            line += (' | excess win%5.1f%% excess mean%+6.2f%%'
                     % (sum(1 for x in ex if x > 0) / len(ex) * 100,
                        sum(ex) / len(ex)))
    if not quiet:
        print(line)
    return dict(n=len(net), win=sum(1 for x in net if x > 0) / len(net) * 100,
                mean=sum(net) / len(net), med=statistics.median(net), pf=pf,
                sd=statistics.pstdev(net))


def tday(rows, key, cost, label, bench=None):
    """t-test on daily aggregated returns (the only honest sample size here)."""
    byd = {}
    for r in rows:
        if r.get(key) is None:
            continue
        val = r[key] - cost * 100
        if bench and r.get(bench) is not None:
            val = r[key] - r[bench] - cost * 100
        byd.setdefault(r['d'], []).append(val)
    if len(byd) < 3:
        return
    s = [sum(v) / len(v) for v in byd.values()]
    m = sum(s) / len(s)
    sd = statistics.pstdev(s)
    t = m / (sd / math.sqrt(len(s))) if sd else 0
    print('   [daily agg] %-26s %d days  mean%+.3f%%/day  sd%.2f%%  t=%5.2f'
          '  positive %d/%d'
          % (label, len(s), m, sd, t, sum(1 for x in s if x > 0), len(s)))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Backtest limit-up "buy the board" strategies on real '
                    'open/close prices with costs and an index benchmark.')
    ap.add_argument('--cost', type=float, default=0.002,
                    help='round-trip cost as a fraction, default 0.002 (0.20%%)')
    args = ap.parse_args(argv)
    cost = args.cost

    cache = YD.read_fixture('zt_study.json')
    kl = YD.read_fixture('zt_kl.json')
    bench = YD.read_fixture('bench_oc.json')
    if not cache or not kl or not bench:
        raise SystemExit('missing data: need zt_study.json, zt_kl.json and '
                         'bench_oc.json in data/ (see docs/02_usage_and_data.md)')
    bo = bench['国证2000']

    days = [k for k in sorted(cache) if cache[k].get('zt')]
    print('window %s ~ %s, %d trading days' % (days[0], days[-1], len(days)))
    print('cost %.2f%% round trip (commission+tax %.1f%% + slippage %.1f%%)\n'
          % (cost * 100, 0.10, cost * 100 - 0.10))

    def kr(code):
        return {r[0].replace('-', ''): r for r in kl.get(code, [])}

    def idx(d):
        return bo.get(d)

    obs = []
    for i, d in enumerate(days):
        if i + 1 >= len(days):
            continue
        nd = days[i + 1]
        d2 = days[i + 2] if i + 2 < len(days) else None
        team = {}
        for x in cache[d]['zt']:
            team[x.get('hybk')] = team.get(x.get('hybk'), 0) + 1
        prev_zb = set()
        if i > 0:
            prev_zb = {x['c'] for x in cache[days[i - 1]]['zb']}
        for x in cache[d]['zt']:
            c = x['c']
            m = kr(c)
            if d not in m or nd not in m:
                continue
            C0, O1, C1 = float(m[d][2]), float(m[nd][1]), float(m[nd][2])
            if min(C0, O1, C1) <= 0:
                continue
            amt = x.get('amount') or 1
            fbt = int(x.get('fbt') or 0)
            rec = dict(d=d, nd=nd, c=c, n=x['n'], lbc=x.get('lbc') or 1,
                       hs=x.get('hs') or 0, fbt=fbt, t=hhmm(fbt),
                       fb=(x.get('fund') or 0) / amt,
                       ltsz=(x.get('ltsz') or 0) / 1e8,
                       sector=x.get('hybk') or '?',
                       team=team.get(x.get('hybk'), 1),
                       zbc=x.get('zbc') or 0,
                       from_zb=c in prev_zb,
                       C0=C0, O1=O1, C1=C1,
                       gap=(O1 / C0 - 1) * 100,
                       day1=(C1 / O1 - 1) * 100,
                       hold1=(C1 / C0 - 1) * 100)
            rec['open_limit_up'] = rec['gap'] >= 9.5   # unbuyable at next open
            rec['close_limit_up'] = rec['hold1'] >= 9.5
            a, b = idx(d), idx(nd)
            if a and b:
                rec['idx_gap'] = (b[0] / a[1] - 1) * 100
                rec['idx_day1'] = (b[1] / b[0] - 1) * 100
                rec['idx_hold1'] = (b[1] / a[1] - 1) * 100
            if d2 and d2 in m:
                rec['hold2'] = (float(m[d2][2]) / O1 - 1) * 100
                c2 = idx(d2)
                if b and c2:
                    rec['idx_hold2'] = (c2[1] / b[1] - 1) * 100
            obs.append(rec)
    used = sorted({r['d'] for r in obs})
    print('sample: %d stock-days (%d trading days -> next day)\n'
          % (len(obs), len(used)))

    print('[0] Fillability: can you even buy on the board?')
    one = [r for r in obs if r['fbt'] <= 93000 and r['zbc'] == 0 and r['fb'] >= 0.15]
    print('   09:25-09:30 sealed, never opened, seal/amount >= 15%%: '
          '%d (%.0f%%) -- effectively unbuyable'
          % (len(one), len(one) / len(obs) * 100))
    buy = [r for r in obs if r['fb'] < 0.15 and r['fbt'] > 93000]
    print('   not a one-word board and seal/amount < 15%% (sellers still '
          'there into the close): %d (%.0f%%) -- the "buyable" subset'
          % (len(buy), len(buy) / len(obs) * 100))
    n_open = sum(1 for r in obs if r['open_limit_up'])
    print('   next session opens at the limit (unbuyable then too): %d (%.0f%%)\n'
          % (n_open, n_open / len(obs) * 100))

    print('[1] Four executable paths (cost %.2f%%)' % (cost * 100))
    stat(buy, 'gap', cost, '  P1 board->next open', 'idx_gap')
    stat(buy, 'hold1', cost, '  P2 board->next close', 'idx_hold1')
    notopen = [r for r in obs if not r['open_limit_up']]
    stat(notopen, 'day1', cost, '  P3 next open->next close', 'idx_day1')
    stat(notopen, 'hold2', cost, '  P4 next open->T+2 close', 'idx_hold2')

    print('\n[2] Daily aggregated t-test (only %d independent observations)'
          % len(used))
    tday(buy, 'gap', cost, 'P1 board->next open', 'idx_gap')
    tday(buy, 'hold1', cost, 'P2 board->next close', 'idx_hold1')
    tday(notopen, 'day1', cost, 'P3 next open->next close', 'idx_day1')
    tday(notopen, 'hold2', cost, 'P4 next open->T+2', 'idx_hold2')

    print('\n[3] Next-session distribution (n=%d)' % len(obs))
    v = [r['hold1'] for r in obs]
    print('   limit-up %5.1f%% | up>3%% %4.1f%% | flat %4.1f%% | '
          'down>3%% %4.1f%% | limit-down %4.1f%%'
          % (sum(1 for x in v if x >= 9.5) / len(v) * 100,
             sum(1 for x in v if x >= 3) / len(v) * 100,
             sum(1 for x in v if -3 < x < 3) / len(v) * 100,
             sum(1 for x in v if x <= -3) / len(v) * 100,
             sum(1 for x in v if x <= -9.5) / len(v) * 100))
    print('   mean %+.2f%% median %+.2f%% sd %.2f%%'
          % (statistics.mean(v), statistics.median(v), statistics.pstdev(v)))

    print('\n[4] By board count (P2)')
    for lo, hi, nm in [(1, 1, 'first board'), (2, 2, '2 boards'),
                       (3, 4, '3-4 boards'), (5, 99, '5+ boards')]:
        stat([r for r in buy if lo <= r['lbc'] <= hi], 'hold1', cost,
             '  ' + nm, 'idx_hold1')

    print('\n[5] By seal time (P2)')
    for lo, hi, nm in [(0, 93500, '09:25-09:35'), (93500, 100000, '09:35-10:00'),
                       (100000, 113000, '10:00-11:30'),
                       (130000, 140000, '13:00-14:00'),
                       (140000, 240000, 'after 14:00')]:
        stat([r for r in buy if lo < r['fbt'] <= hi], 'hold1', cost,
             '  ' + nm, 'idx_hold1')

    print('\n[6] By seal/amount (P2, all samples)')
    for lo, hi, nm in [(0, 0.05, '<5% (weak)'), (0.05, 0.15, '5-15%'),
                       (0.15, 0.5, '15-50%'), (0.5, 99, '>=50% (one-word)')]:
        stat([r for r in obs if lo <= r['fb'] < hi], 'hold1', cost,
             '  ' + nm, 'idx_hold1')

    print('\n[7] By sector breadth (P2)')
    for lo, hi, nm in [(1, 1, 'alone (1)'), (2, 2, '2 names'),
                       (3, 4, '3-4 names'), (5, 99, '5+ (main line)')]:
        stat([r for r in buy if lo <= r['team'] <= hi], 'hold1', cost,
             '  ' + nm, 'idx_hold1')

    print('\n[8] Broke the board yesterday? (P2)')
    stat([r for r in buy if r['from_zb']], 'hold1', cost,
         '  broke yesterday', 'idx_hold1')
    stat([r for r in buy if not r['from_zb']], 'hold1', cost,
         '  did not break', 'idx_hold1')

    print('\n[9] By turnover (P2)')
    for lo, hi, nm in [(0, 5, '<5%'), (5, 10, '5-10%'), (10, 20, '10-20%'),
                       (20, 999, '>=20%')]:
        stat([r for r in buy if lo <= r['hs'] < hi], 'hold1', cost,
             '  ' + nm, 'idx_hold1')

    print('\n[10] By float mcap (P2)')
    for lo, hi, nm in [(0, 30, '<30Yi'), (30, 60, '30-60Yi'),
                       (60, 120, '60-120Yi'), (120, 99999, '>=120Yi')]:
        stat([r for r in buy if lo <= r['ltsz'] < hi], 'hold1', cost,
             '  ' + nm, 'idx_hold1')

    print('\n[11] Portfolio: equal-weight every buyable name each day (P2)')
    byd = {}
    for r in buy:
        byd.setdefault(r['d'], []).append(r)
    nets = []
    for d in sorted(byd):
        v = byd[d]
        net = sum(x['hold1'] for x in v) / len(v) - cost * 100
        bch = sum(x.get('idx_hold1', 0) for x in v) / len(v)
        nets.append(net - bch)
        print('   %s  n=%-2d portfolio%+7.2f%%  index%+7.2f%%  excess%+7.2f%%'
              % (d, len(v), net, bch, net - bch))
    m = sum(nets) / len(nets)
    sd = statistics.pstdev(nets)
    print('   -> daily excess %+.3f%% / day win %.0f%% / t=%.2f / cumulative %+.1f%%'
          % (m, sum(1 for x in nets if x > 0) / len(nets) * 100,
             m / (sd / math.sqrt(len(nets))), sum(nets)))

    print('\nobservation dump -> %s' % YD.write_json('zt_obs.json', obs))


if __name__ == '__main__':
    main()
