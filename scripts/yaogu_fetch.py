"""HTTP fetchers for the per-stock lookups, with the fallbacks that actually work.

Two hard-won details live here:

  * Eastmoney's ``push2his`` K-line endpoint rate-limits hard. After a few
    hundred requests it starts answering ``Remote end closed connection without
    response``. Tencent's ``web.ifzq.gtimg.cn`` serves the same daily bars and
    keeps working, so every K-line read tries Eastmoney first and falls back.
  * Announcement *bodies* (not just titles) are available at
    ``np-cnotice-stock.eastmoney.com`` -- that is how the 600661 buyback-sale
    case was cracked. See docs/06_case_600661.md.

Standard library only.
"""
import json
import time
import urllib.request

UA = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'}

EAST_KLINE = ('https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s'
              '&fields1=f1,f2,f3,f4,f5'
              '&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'
              '&klt=101&fqt=1&end=20500101&lmt=%d')
TENCENT_KLINE = ('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
                 '?param=%s,day,,,%d,qfq')
NOTICE_LIST = ('https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1'
               '&page_size=50&page_index=1&ann_type=A&client_source=web'
               '&stock_list=%s')
NOTICE_BODY = ('https://np-cnotice-stock.eastmoney.com/api/content/ann'
               '?art_code=%s&client_source=web&page_index=1')


def market(code):
    """'sh' / 'sz' prefix for Tencent, and the Eastmoney secid prefix."""
    return 'sh' if code[0] in '695' else 'sz'


def secid(code):
    return ('1.' if code[0] in '695' else '0.') + code


def try_get(url, tries=2, wait=0.6):
    """GET + parse JSON, returning None instead of killing the process."""
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            raw = urllib.request.urlopen(req, timeout=20).read()
            return json.loads(raw.decode('utf-8', 'ignore'))
        except Exception:  # noqa: BLE001
            time.sleep(wait)
    return None


def kline_eastmoney(code, lmt=140):
    d = try_get(EAST_KLINE % (secid(code), lmt))
    kl = ((d or {}).get('data') or {}).get('klines') or []
    return [p.split(',') for p in kl]   # [date, open, close, high, low, vol, amt, ..., pct, ..., turn]


def kline_tencent(code, lmt=140):
    sym = market(code) + code
    d = try_get(TENCENT_KLINE % (sym, lmt))
    node = ((d or {}).get('data') or {}).get(sym) or {}
    rows = node.get('qfqday') or node.get('day') or []
    # ["2026-10-21", open, close, high, low, volume, ...]
    return [list(r[:6]) for r in rows if len(r) >= 6]


def kline(code, lmt=140):
    """Daily bars as {YYYYMMDD: [date, open, close, high, low, vol]}.

    Tries Eastmoney, falls back to Tencent when it is being rate-limited.
    """
    rows = kline_eastmoney(code, lmt) or kline_tencent(code, lmt)
    out = {}
    for r in rows:
        if len(r) < 3:
            continue
        out[str(r[0]).replace('-', '')] = r
    return out


def notices(code, limit=50):
    """[(YYYY-MM-DD, title)] newest first."""
    items = ((try_get(NOTICE_LIST % code) or {}).get('data') or {}).get('list') or []
    return [((it.get('notice_date') or '')[:10], it.get('title') or '')
            for it in items][:limit]


def notice_bodies(code, limit=5, keyword=None):
    """Full announcement text for the newest notices (optionally filtered).

    Returns [(YYYY-MM-DD, title, text)] with HTML tags stripped. This is the
    call that exposes things a title never says, e.g. how many repurchased
    shares the company sold into the board and at what price.
    """
    items = ((try_get(NOTICE_LIST % code) or {}).get('data') or {}).get('list') or []
    out = []
    for it in items:
        title = it.get('title') or ''
        if keyword and keyword not in title:
            continue
        art = it.get('art_code')
        if not art:
            continue
        body = ((try_get(NOTICE_BODY % art) or {}).get('data') or {})
        text = body.get('notice_content') or ''
        text = _strip_html(text)
        out.append(((it.get('notice_date') or '')[:10], title, text))
        if len(out) >= limit:
            break
        time.sleep(0.2)
    return out


def _strip_html(s):
    out = []
    depth = 0
    for ch in s:
        if ch == '<':
            depth += 1
            continue
        if ch == '>':
            depth = max(0, depth - 1)
            continue
        if depth == 0:
            out.append(ch)
    text = ''.join(out)
    for a, b in (('&nbsp;', ' '), ('&amp;', '&'), ('&lt;', '<'),
                 ('&gt;', '>'), ('&quot;', '"')):
        text = text.replace(a, b)
    return ' '.join(text.split())
