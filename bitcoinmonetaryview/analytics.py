# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only queries over the results database for the web API."""

import csv
import io
import json
import os
import sqlite3
import threading
import time

from .rules import monetary_rules as mr
from .store import BLOCK_COLS, BUCKET

BLOCKS_PER_YEAR = 52560
AGE_BANDS = (("2+ years", 2 * BLOCKS_PER_YEAR), ("1–2 years", BLOCKS_PER_YEAR),
             ("6–12 months", BLOCKS_PER_YEAR // 2), ("under 6 months", 0))
SUM_COLS = ("size", "stored", "weight", "tx_count", "envelope", "op_return", "multisig", "scriptsig",
            "whole", "modified", "stripped", "dust_outputs", "filter_entries", "retained_protocol")


def csv_safe(v):
    """Neutralise spreadsheet formula injection."""
    s = str(v)
    if s and s[0] in "=+-@\t\r":
        return "'" + s
    return s


class Analytics:
    def __init__(self, db_path):
        self.db_path = db_path
        self._local = threading.local()
        self._cache = {}

    def db(self):
        c = getattr(self._local, "db", None)
        if c is None or getattr(self._local, "path", None) != self.db_path:
            if not os.path.exists(self.db_path):
                return None
            c = sqlite3.connect("file:" + self.db_path + "?mode=ro", uri=True, timeout=10,
                                check_same_thread=False)
            self._local.db, self._local.path = c, self.db_path
        return c

    def cached(self, key, ttl, fn):
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        val = fn()
        self._cache[key] = (time.monotonic(), val)
        return val

    def meta(self, db):
        return dict(db.execute("SELECT key, value FROM meta").fetchall())

    # ------------------------------------------------------------------ summary
    def summary(self):
        return self.cached("summary", 3, self._summary)

    def _summary(self):
        db = self.db()
        if db is None:
            return {"empty": True}
        m = self.meta(db)
        cols = ",".join(f"COALESCE(SUM({c}),0)" for c in SUM_COLS)
        r = db.execute(f"SELECT COUNT(*), MIN(height), MAX(height), {cols} FROM blocks").fetchone()
        count, lo, hi = r[0], r[1], r[2]
        tot = dict(zip(SUM_COLS, r[3:]))
        if not count:
            return {"empty": True, "meta": _public_meta(m)}
        full = db.execute(f"SELECT COUNT(*), {cols} FROM blocks WHERE utxo_done=1").fetchone()
        spam = sum(tot[k] for k in mr.CARRIERS)
        txs = tot["tx_count"] or 1
        out = {
            "empty": False,
            "blocks_scanned": count, "lowest": lo, "highest": hi,
            "full_blocks": full[0],
            "totals": tot,
            "spam_bytes": spam,
            "spam_pct": spam / tot["size"] * 100 if tot["size"] else 0,
            "saved_bytes": tot["size"] - tot["stored"],
            "saved_pct": (tot["size"] - tot["stored"]) / tot["size"] * 100 if tot["size"] else 0,
            "modified_tx_pct": (tot["modified"] + tot["stripped"]) / txs * 100,
            "by_carrier": {k: tot[k] for k in mr.CARRIERS},
            "meta": _public_meta(m),
            "utxo": self._utxo(db, m, hi),
        }
        return out

    def _utxo(self, db, m, tip):
        rows = db.execute("SELECT bucket, kind, count, bytes FROM utxo_hist").fetchall()
        kinds = {}
        bands = {name: {"count": 0, "bytes": 0} for name, _ in AGE_BANDS}
        total_c = total_b = 0
        for bucket, kind, c, b in rows:
            if c <= 0:
                continue
            k = mr.UTXO_KIND_NAMES.get(kind, str(kind))
            kinds.setdefault(k, {"count": 0, "bytes": 0})
            kinds[k]["count"] += c
            kinds[k]["bytes"] += b
            total_c += c
            total_b += b
            age = max(0, (tip or 0) - (bucket * BUCKET + BUCKET // 2))
            for name, lower in AGE_BANDS:
                if age >= lower:
                    bands[name]["count"] += c
                    bands[name]["bytes"] += b
                    break
        info = None
        if m.get("utxo_info"):
            try:
                info = json.loads(m["utxo_info"])
            except ValueError:
                info = None
        res = {"count": total_c, "bytes": total_b,
               "disk_estimate": int(total_b * mr.LEVELDB_OVERHEAD_FACTOR),
               "by_kind": kinds, "age_bands": [{"band": n, **bands[n]} for n, _ in AGE_BANDS],
               "complete": m.get("utxo_complete") == "1" and m.get("full_done") == "1",
               "full_done": m.get("full_done") == "1",
               "node_utxo": None}
        if info:
            res["node_utxo"] = {"txouts": info.get("txouts"), "bogosize": info.get("bogosize"),
                                "disk_size": info.get("disk_size"), "height": info.get("height"),
                                "time": float(m.get("utxo_info_time", 0))}
            if info.get("txouts"):
                res["share_of_entries_pct"] = total_c / info["txouts"] * 100
        return res

    # ------------------------------------------------------------------ blocks
    def blocks(self, limit=50, before=None):
        db = self.db()
        if db is None:
            return []
        limit = max(1, min(int(limit), 500))
        if before is None:
            rows = db.execute(f"SELECT {','.join(BLOCK_COLS)} FROM blocks ORDER BY height DESC LIMIT ?",
                              (limit,)).fetchall()
        else:
            rows = db.execute(f"SELECT {','.join(BLOCK_COLS)} FROM blocks WHERE height<? "
                              f"ORDER BY height DESC LIMIT ?", (int(before), limit)).fetchall()
        return [_block_dict(r) for r in rows]

    def block(self, height):
        db = self.db()
        if db is None:
            return None
        r = db.execute(f"SELECT {','.join(BLOCK_COLS)} FROM blocks WHERE height=?", (int(height),)).fetchone()
        return None if r is None else _block_dict(r)

    def range(self, lo, hi, points=400):
        """Per-block (or bucketed) carrier bytes for a height range, for the bar chart."""
        db = self.db()
        if db is None:
            return {"buckets": []}
        lo, hi = int(lo), int(hi)
        if hi < lo:
            lo, hi = hi, lo
        points = max(10, min(int(points), 2000))
        size = max(1, -(-(hi - lo + 1) // points))
        rows = db.execute(
            "SELECT (height-?)/? AS b, MIN(height), MAX(height), COUNT(*), SUM(size), SUM(stored), "
            "SUM(envelope), SUM(op_return), SUM(multisig), SUM(scriptsig) FROM blocks "
            "WHERE height BETWEEN ? AND ? GROUP BY b ORDER BY b", (lo, size, lo, hi)).fetchall()
        return {"bucket_size": size, "buckets": [
            {"from": r[1], "to": r[2], "blocks": r[3], "size": r[4], "stored": r[5],
             "envelope": r[6], "op_return": r[7], "multisig": r[8], "scriptsig": r[9]} for r in rows]}

    def history(self):
        return self.cached("history", 30, self._history)

    def _history(self):
        db = self.db()
        if db is None:
            return {"months": []}
        rows = db.execute(
            "SELECT strftime('%Y-%m', time, 'unixepoch') AS m, COUNT(*), SUM(size), SUM(stored), "
            "SUM(envelope), SUM(op_return), SUM(multisig), SUM(scriptsig), "
            "SUM(CASE WHEN utxo_done=1 THEN utxo_added-utxo_spent ELSE 0 END), "
            "SUM(CASE WHEN utxo_done=1 THEN utxo_added_bytes-utxo_spent_bytes ELSE 0 END), "
            "MIN(height), MAX(height) FROM blocks GROUP BY m ORDER BY m").fetchall()
        months = []
        cum_c = cum_b = 0
        for r in rows:
            cum_c += r[8]
            cum_b += r[9]
            months.append({"month": r[0], "blocks": r[1], "size": r[2], "stored": r[3], "envelope": r[4],
                           "op_return": r[5], "multisig": r[6], "scriptsig": r[7],
                           "utxo_count": cum_c, "utxo_bytes": cum_b, "from": r[10], "to": r[11]})
        return {"months": months}

    # ------------------------------------------------------------------ export
    def export_csv(self):
        db = self.db()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["height", "hash", "time_utc", *[c for c in BLOCK_COLS if c not in ("height", "hash", "time")]])
        if db is not None:
            cur = db.execute(f"SELECT {','.join(BLOCK_COLS)} FROM blocks ORDER BY height")
            for r in cur:
                d = _block_dict(r)
                w.writerow([csv_safe(d["height"]), csv_safe(d["hash"]),
                            csv_safe(time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(d["time"]))),
                            *[csv_safe(d[c]) for c in BLOCK_COLS if c not in ("height", "hash", "time")]])
        return buf.getvalue()

    def export_json(self):
        db = self.db()
        blocks = []
        if db is not None:
            cur = db.execute(f"SELECT {','.join(BLOCK_COLS)} FROM blocks ORDER BY height")
            blocks = [_block_dict(r) for r in cur]
        return {"summary": self._summary(), "blocks": blocks}


def _block_dict(r):
    d = dict(zip(BLOCK_COLS, r))
    d["hash"] = mr.hash_to_hex(bytes(d["hash"]))
    spam = sum(d[k] or 0 for k in mr.CARRIERS)
    d["spam_bytes"] = spam
    d["spam_pct"] = spam / d["size"] * 100 if d["size"] else 0
    d["utxo_done"] = bool(d["utxo_done"])
    return d


def _public_meta(m):
    keep = ("full_next_height", "full_start_height", "full_done", "quick_done", "utxo_complete", "rules_id")
    return {k: m.get(k) for k in keep}
