"""Shared data locations for the yaogu-screening scripts.

Reads merge two layers, the later one wins per file:

    data/                  shipped in the repo -- read only, never written
    ~/.cache/yaogu/        writable user cache

Writes only ever go to the user cache. The shipped files contain a historical
window that the Eastmoney pool API no longer serves (~1 month retention), so
they cannot be re-fetched and must not be overwritten.

Override the two directories with YAOGU_DATA and YAOGU_CACHE_DIR.
"""
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED_DIR = os.environ.get('YAOGU_DATA') or os.path.join(REPO, 'data')
CACHE_DIR = (os.environ.get('YAOGU_CACHE_DIR')
             or os.path.join(os.path.expanduser('~'), '.cache', 'yaogu'))


def _candidates(name):
    return [os.path.join(CACHE_DIR, name), os.path.join(SEED_DIR, name)]


def path_of(name):
    """First existing location of a data file (user cache first, then seed)."""
    for p in _candidates(name):
        if os.path.exists(p):
            return p
    return None


def read_json(name, default=None):
    p = path_of(name)
    if p is None:
        return default
    with open(p, encoding='utf-8') as fh:
        return json.load(fh)


def read_fixture(name, default=None):
    """Read the shipped fixture only, ignoring the user cache.

    Used by the backtests: their inputs are frozen research snapshots (pool
    records plus the K-lines fetched for exactly those names), so mixing in a
    freshly accumulated user cache would silently change the sample and make
    the documented numbers impossible to reproduce.
    """
    p = os.path.join(SEED_DIR, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding='utf-8') as fh:
        return json.load(fh)


def write_json(name, obj):
    """Write to the user cache and return the path written."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = os.path.join(CACHE_DIR, name)
    with open(p, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, ensure_ascii=False)
    return p
