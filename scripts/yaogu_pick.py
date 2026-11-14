#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Second-pass check on the limit-up candidates that scored >= 7.

scan_zt_pool.py grades the pool with numbers only. This script adds the three
things a number cannot tell you, plus two that turned out to matter:

  * announcements      -- risk warnings, incentive plans, restructuring
  * relative strength  -- is the target the weakest name in a hot sector?
  * event calendar     -- earnings date, shareholder meeting
  * K-line shape       -- 5/20/60-day returns, 60-day box width, MA20, %b
  * dragon-tiger list  -- net buy/sell sequence (not a single day)
  * share-buyback sale -- is the company itself selling into the board?

The last one came out of a real case (600661, 2026-10): the company had
pre-announced a buyback-share sale plan, then sold 1.5m shares *on the limit-up
board*. See docs/06_case_600661.md.

Usage:
    python3 scripts/yaogu_pick.py                 # today
    python3 scripts/yaogu_pick.py 20261021
    python3 scripts/yaogu_pick.py 20261021 --min-score 7 --json out/pick.json

Standard library only.
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.request
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaogu_fetch as YF  # noqa: E402
from scan_zt_pool import pool, score, hhmm  # noqa: E402

RISK_KW = ('风险提示', '异常波动', '停牌', '核查', '严重异常', '关注函',
           '监管工作函', '警示', '减持')
NOISE_KW = ('保荐', '持续督导', '核查意见', '上市保荐', '验资', '法律意见',
            '审计报告')
GOOD_KW = ('激励', '重组', '定增', '向特定对象发行', '控股股东', '变更',
           '中标', '摘帽', '撤销')
# The single most valuable exclusion found so far: a company selling its own
# repurchased shares is a known seller with a price anchor, and it is easy to
# miss because the title does not say "减持".
BUYBACK_SALE_KW = ('出售回购', '集中竞价出售', '回购股份出售', '回购股份进展')


def g(url, tries=3):
    return YF.try_get(url, tries=tries) or {'_err': 'fetch failed'}


def kline(code, lmt=140):
    """Ascending daily bars; Eastmoney first, Tencent as fallback."""
    rows = YF.kline(code, lmt)
    out = []
    for d in sorted(rows):
        p = rows[d]
        out.append({'d': d, 'o': float(p[1]), 'c': float(p[2]),
                    'h': float(p[3]) if len(p) > 3 else None,
                    'l': float(p[4]) if len(p) > 4 else None,
                    'vol': float(p[5]) if len(p) > 5 else 0.0,
                    'amt': float(p[6]) if len(p) > 6 else 0.0,
                    'pct': float(p[8]) if len(p) > 8 else 0.0,
                    'turn': float(p[10]) if len(p) > 10 else 0.0})
    return out


def notices(code, keep_days=60):
    floor = (datetime.date.today() - datetime.timedelta(days=keep_days)).isoformat()
    return [(d, t.replace('\u3000', ' ')) for d, t in YF.notices(code)
            if d >= floor]


def lhb(code, n=6):
    url = ('https://datacenter-web.eastmoney.com/api/data/v1/get?'
           'reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL'
           '&pageSize=%d&sortColumns=TRADE_DATE&sortTypes=-1&source=WEB'
           '&client=WEB&filter=(SECURITY_CODE%%3D%%22%s%%22)' % (n, code))
    rows = ((g(url).get('result') or {}).get('data') or [])
    return [(r.get('TRADE_DATE', '')[:10], r.get('EXPLANATION') or '',
             round((r.get('BILLBOARD_NET_AMT') or 0) / 1e8, 2)) for r in rows]


def appoint(code):
    url = ('https://datacenter-web.eastmoney.com/api/data/v1/get?'
           'reportName=RPT_PUBLIC_BS_APPOIN&columns=ALL&pageSize=5'
           '&sortColumns=REPORT_DATE&sortTypes=-1&source=WEB&client=WEB'
           '&filter=(SECURITY_CODE%%3D%%22%s%%22)' % code)
    rows = ((g(url).get('result') or {}).get('data') or [])
    return [((r.get('REPORT_DATE') or '')[:10],
             (r.get('FIRST_APPOINT_DATE') or '')[:10],
             (r.get('ACTUAL_PUBLISH_DATE') or '')[:10]) for r in rows[:3]]


def enrich(x, sc, grade, flags):
    code, name = x['c'], x['n']
    lbc = int(float(x.get('lbc') or 1))
    ltsz = float(x.get('ltsz') or 0) / 1e8
    fund = float(x.get('fund') or 0) / 1e8
    amount = float(x.get('amount') or 0) / 1e8
    rec = {'code': code, 'name': name, 'score': sc, 'grade': grade,
           'boards': lbc, 'seal_at': hhmm(x.get('fbt') or 0),
           'turnover': float(x.get('hs') or 0),
           'seal_pct': round(fund / ltsz * 100, 2) if ltsz else 0.0,
           'seal_amt_pct': round(fund / amount * 100, 1) if amount else 0.0,
           'float_yi': round(ltsz, 1), 'broke': int(float(x.get('zbc') or 0)),
           'sector': x.get('hybk'), 'flags': flags,
           'price': round(float(x.get('p') or 0) / 1000, 2),
           'amount_yi': round(amount, 2), 'fund_yi': round(fund, 2)}

    kl = kline(code)
    if kl:
        closes = [k['c'] for k in kl]
        rec['pct5'] = round((closes[-1] / closes[-6] - 1) * 100, 1) if len(closes) > 6 else None
        rec['pct20'] = round((closes[-1] / closes[-21] - 1) * 100, 1) if len(closes) > 21 else None
        rec['pct60'] = round((closes[-1] / closes[-61] - 1) * 100, 1) if len(closes) > 61 else None
        w = closes[-60:]
        rec['box60'] = round((max(w) / min(w) - 1) * 100, 1) if w else None
        rec['ma20'] = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else None
        rec['board_cum_pct'] = (round((closes[-1] / closes[-lbc - 1] - 1) * 100, 1)
                                if len(closes) > lbc + 1 else None)

    ns = notices(code)
    rec['notice_n'] = len(ns)
    rec['risk_notice'] = [t for d, t in ns
                          if not any(k in t for k in NOISE_KW)
                          and any(k in t for k in RISK_KW)][:4]
    rec['good_notice'] = [t for d, t in ns if any(k in t for k in GOOD_KW)][:4]
    rec['buyback_sale'] = [('%s %s' % (d, t)) for d, t in ns
                           if any(k in t for k in BUYBACK_SALE_KW)][:3]
    rec['lhb'] = lhb(code, 5)
    rec['appoint'] = appoint(code)
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Second-pass check on high-scoring limit-up candidates.')
    ap.add_argument('date', nargs='?', default=None,
                    help='trading day as YYYYMMDD (default: today)')
    ap.add_argument('--min-score', type=int, default=7,
                    help='minimum score to check (default 7)')
    ap.add_argument('--json', dest='json_out', metavar='PATH',
                    help='where to write the result JSON '
                         '(default out/pick_<date>.json)')
    args = ap.parse_args(argv)

    date = args.date or datetime.datetime.now().strftime('%Y%m%d')
    zt = pool('getTopicZTPool', date)
    if not zt:
        raise SystemExit('No limit-up pool for %s (weekend / holiday / older '
                         'than the ~1 month retention).' % date)
    hy = Counter(x.get('hybk', '') for x in zt)
    rows = []
    for x in zt:
        sc, grade, flags, seal_amt = score(x, hy.get(x.get('hybk', ''), 1))
        rows.append((sc, grade, flags, x))
    rows.sort(key=lambda r: -r[0])

    pick = [r for r in rows if r[0] >= args.min_score]
    print('candidates with score >= %d: %d' % (args.min_score, len(pick)))

    out = []
    for sc, grade, flags, x in pick:
        r = enrich(x, sc, grade, flags)
        out.append(r)
        print('=' * 88)
        print('%s %s  %s %+d  %d board(s)  seal@%s  turn %.1f%%  '
              'seal/float %.2f%%  seal/amount %.1f%%  float %.1fYi  '
              'broke %d  sector %s'
              % (r['code'], r['name'], r['grade'], r['score'], r['boards'],
                 r['seal_at'], r['turnover'], r['seal_pct'],
                 r['seal_amt_pct'], r['float_yi'], r['broke'], r['sector']))
        print('   price %.2f  amount %.2fYi  seal order %.2fYi | '
              '5d %+.1f%%  20d %+.1f%%  60d %+.1f%% | 60d box %.1f%%  MA20 %.2f'
              % (r['price'], r['amount_yi'], r['fund_yi'], r.get('pct5') or 0,
                 r.get('pct20') or 0, r.get('pct60') or 0,
                 r.get('box60') or 0, r.get('ma20') or 0))
        print('   run so far %+.1f%% | %d announcement(s)'
              % (r.get('board_cum_pct') or 0, r['notice_n']))
        for t in r['buyback_sale']:
            print('   [BUYBACK SALE] %s' % t[:70])
        for t in r['risk_notice']:
            print('   [risk notice] %s' % t[:70])
        for t in r['good_notice']:
            print('   [event] %s' % t[:70])
        if r['lhb']:
            print('   dragon-tiger: ' + ' | '.join('%s %+.2fYi' % (d, n)
                                                   for d, _, n in r['lhb']))
        else:
            print('   dragon-tiger: no record in the last 5 sessions')
        if r['appoint']:
            print('   report dates: ' + ' | '.join(
                'period %s appoint %s actual %s' % a for a in r['appoint'][:2]))
        time.sleep(0.25)

    path = args.json_out or os.path.join('out', 'pick_%s.json' % date)
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print('\nJSON written to %s' % path)


if __name__ == '__main__':
    main()
