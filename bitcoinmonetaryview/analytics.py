# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only queries over the results database for the web API."""

import contextlib
import csv
import io
import json
import math
import os
import sqlite3
import threading
import time

from .rules import monetary_rules as mr
from .store import BLOCK_COLS, BUCKET

BLOCKS_PER_YEAR = 52560
BLOCK_SPACING = 600          # target seconds between blocks
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
    POOL_SIZE = 4       # idle read-only connections kept for reuse
    LARGE = 100_000     # from this many scanned blocks the slow queries are cached longer

    def __init__(self, db_path):
        self.db_path = db_path
        self._local = threading.local()
        self._cache = {}
        self._lock = threading.Lock()
        self._idle = []
        self._open = set()
        self._closed = False
        self._blocks = 0

    def db(self):
        """The calling thread's read-only connection, taken from the pool until release()."""
        c = getattr(self._local, "db", None)
        if c is not None:
            return c
        if not self.db_path or not os.path.exists(self.db_path):
            return None
        with self._lock:
            if self._closed:
                return None
            c = self._idle.pop() if self._idle else None
            if c is None:
                c = sqlite3.connect("file:" + self.db_path + "?mode=ro", uri=True, timeout=10,
                                    check_same_thread=False)
                self._open.add(c)
        self._local.db = c
        return c

    def release(self):
        """Return the calling thread's connection to the pool (the server calls this after
        every request)."""
        c = getattr(self._local, "db", None)
        if c is None:
            return
        self._local.db = None
        try:
            if c.in_transaction:
                c.rollback()
        except sqlite3.Error:
            pass
        with self._lock:
            if not self._closed and len(self._idle) < self.POOL_SIZE:
                self._idle.append(c)
                return
            self._open.discard(c)
        c.close()

    def close(self):
        """Close every connection: idle ones now, ones still in use when they are released."""
        with self._lock:
            self._closed = True
            idle, self._idle = self._idle, []
            for c in idle:
                self._open.discard(c)
        for c in idle:
            c.close()

    def open_connections(self):
        with self._lock:
            return len(self._open)

    CACHE_ENTRIES = 128

    def cached(self, key, ttl, fn):
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        val = fn()
        if len(self._cache) >= self.CACHE_ENTRIES:
            for k in sorted(self._cache, key=lambda k: self._cache[k][0])[:self.CACHE_ENTRIES // 2]:
                self._cache.pop(k, None)
        self._cache[key] = (time.monotonic(), val)
        return val

    @contextlib.contextmanager
    def snapshot(self, db):
        """One read transaction: every query inside sees the same committed state, even while
        the scanner commits in between (otherwise e.g. the scan cursor and the block table
        could come from different moments and the estimate would briefly vanish)."""
        if db.in_transaction:
            db.rollback()
        db.execute("BEGIN")
        try:
            yield db
        finally:
            db.rollback()

    def meta(self, db):
        return dict(db.execute("SELECT key, value FROM meta").fetchall())

    # ------------------------------------------------------------------ summary
    def ttl(self, small, large):
        return large if self._blocks >= self.LARGE else small

    def summary(self):
        return self.cached("summary", self.ttl(3, 15), self._summary)

    def _summary(self):
        db = self.db()
        if db is None:
            return {"empty": True}
        with self.snapshot(db):
            return self._summary_in(db)

    def _summary_in(self, db):
        m = self.meta(db)
        cols = ",".join(f"COALESCE(SUM({c}),0)" for c in SUM_COLS)
        r = db.execute(f"SELECT COUNT(*), MIN(height), MAX(height), {cols} FROM blocks").fetchone()
        count, lo, hi = r[0], r[1], r[2]
        self._blocks = count
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
            "estimate": self._estimate(db, m, count, tot),
            # the whole-chain estimate is still being prepared (sample pass not finished)
            "estimate_pending": m.get("full_done") != "1" and m.get("sample_done") != "1",
            "coverage": self._coverage(db, m),
        }
        return out

    def _coverage(self, db, m):
        """Which heights have exact results: the full scan's range plus the quick pass at the top.
        `gap` is the part of the history in between that has not been scanned yet."""
        full_done = m.get("full_done") == "1"
        start = int(m.get("full_start_height") or 0)
        nxt = int(m.get("full_next_height") or start)
        top = db.execute("SELECT MAX(height) FROM blocks").fetchone()[0]
        ranges = []
        if nxt > start:
            ranges.append([start, nxt - 1])
        gap = None
        if not full_done and top is not None:
            low = db.execute("SELECT MIN(height) FROM blocks WHERE height>=?", (nxt,)).fetchone()[0]
            if low is not None:
                ranges.append([low, top])
                gap = {"from": nxt, "to": low - 1}
            else:
                gap = {"from": nxt, "to": None}
            if gap["to"] is not None and gap["to"] < gap["from"]:
                gap = None
            if gap:
                t0 = db.execute("SELECT time FROM blocks WHERE height=?", (nxt - 1,)).fetchone()
                t1 = db.execute("SELECT time FROM blocks WHERE height=?", (gap["to"] + 1,)).fetchone() \
                    if gap["to"] is not None else None
                gap["from_time"] = t0[0] if t0 else None
                gap["to_time"] = t1[0] if t1 else None
        elif top is not None and not ranges:
            ranges.append([start, top])
        return {"complete": full_done, "ranges": ranges, "gap": gap}

    def _estimate(self, db, m, count, tot):
        """
        Whole-chain estimate while the full scan is still running: the exact results so far plus
        the evenly spaced sample scaled up to the history that has not been scanned yet. The
        sample only stands in for blocks without exact results, so the estimate converges to the
        exact figures as the full scan proceeds. None once the full scan is complete.
        """
        if m.get("full_done") == "1" or m.get("sample_done") != "1" or not m.get("sample_top"):
            return None
        g0 = int(m.get("full_next_height") or m.get("full_start_height") or 0)
        low = db.execute("SELECT MIN(height) FROM blocks WHERE height>=?", (g0,)).fetchone()[0]
        end = low if low is not None else int(m["sample_top"])
        gap = end - g0
        if gap <= 0:
            return None
        spam_expr = "+".join(f"COALESCE({k},0)" for k in mr.CARRIERS)
        cols = ",".join(f"COALESCE(SUM({c}),0)" for c in SUM_COLS)
        r = db.execute(f"SELECT COUNT(*), {cols}, COALESCE(SUM(({spam_expr})*({spam_expr})),0), "
                       f"COALESCE(SUM((size-stored)*(size-stored)),0) FROM sample WHERE height>=? AND height<?",
                       (g0, end)).fetchone()
        n = r[0]
        if not n:
            return None
        part = dict(zip(SUM_COLS, r[1:1 + len(SUM_COLS)]))
        spam_sq, saved_sq = r[-2], r[-1]
        scale = gap / n
        est = {k: tot[k] + part[k] * scale for k in SUM_COLS}

        def margin(total, sq):
            # 95 % margin of the scaled-up part (sampling error with finite-population correction)
            if n < 2:
                return None
            var = max(0.0, (sq - total * total / n) / (n - 1))
            return 1.96 * gap * math.sqrt(var / n) * math.sqrt(max(0.0, 1 - n / gap))

        spam = sum(est[k] for k in mr.CARRIERS)
        saved = est["size"] - est["stored"]
        spam_m = margin(sum(part[k] for k in mr.CARRIERS), spam_sq)
        saved_m = margin(part["size"] - part["stored"], saved_sq)
        txs = est["tx_count"] or 1
        return {
            "totals": est,
            "blocks": count + gap,
            "samples": n,
            "sample_every": int(m.get("sample_every") or 0),
            "exact_share_pct": tot["size"] / est["size"] * 100 if est["size"] else 0,
            "spam_bytes": spam,
            "spam_pct": spam / est["size"] * 100 if est["size"] else 0,
            "spam_margin_pct": spam_m / spam * 100 if spam_m is not None and spam else None,
            "saved_bytes": saved,
            "saved_pct": saved / est["size"] * 100 if est["size"] else 0,
            "saved_margin_pct": saved_m / saved * 100 if saved_m is not None and saved > 0 else None,
            "modified_tx_pct": (est["modified"] + est["stripped"]) / txs * 100,
            "by_carrier": {k: est[k] for k in mr.CARRIERS},
        }

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
               "disk_estimate": round(total_b * mr.LEVELDB_OVERHEAD_FACTOR),   # rounded like the per-type figures
               "by_kind": kinds, "age_bands": [{"band": n, **bands[n]} for n, _ in AGE_BANDS],
               "complete": m.get("utxo_complete") == "1" and m.get("full_done") == "1",
               "full_done": m.get("full_done") == "1",
               "node_utxo": None}
        if info:
            res["node_utxo"] = {"txouts": info.get("txouts"), "bogosize": info.get("bogosize"),
                                "disk_size": info.get("disk_size"), "height": info.get("height"),
                                "time": float(m.get("utxo_info_time", 0))}
            if info.get("txouts") and total_c <= info["txouts"]:
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

    def block_by_hash(self, hexhash):
        db = self.db()
        if db is None:
            return None
        r = db.execute(f"SELECT {','.join(BLOCK_COLS)} FROM blocks WHERE hash=?",
                       (mr.hex_to_hash(hexhash),)).fetchone()
        return None if r is None else _block_dict(r)

    def period(self, start=None, end=None, lo=None, hi=None):
        return self.cached(("period", start, end, lo, hi), 3, lambda: self._period(start, end, lo, hi))

    def _period(self, start=None, end=None, lo=None, hi=None):
        """
        Exact statistics for a time range (unix seconds, inclusive) or a height range, from the
        blocks scanned so far, plus how much of the range that covers. For time ranges the
        number of blocks in the range is interpolated from known block times when not all of
        it has been scanned yet.
        """
        db = self.db()
        if db is None:
            return {"empty": True}
        with self.snapshot(db):
            cols = ",".join(f"COALESCE(SUM({c}),0)" for c in SUM_COLS)
            if lo is not None:
                lo, hi = int(lo), int(hi)
                if hi < lo:
                    lo, hi = hi, lo
                where, args = "height BETWEEN ? AND ?", (lo, hi)
            else:
                start, end = int(start), int(end)
                if end < start:
                    start, end = end, start
                where, args = "time BETWEEN ? AND ?", (start, end)
            r = db.execute(f"SELECT COUNT(*), MIN(height), MAX(height), MIN(time), MAX(time), {cols} "
                           f"FROM blocks WHERE {where}", args).fetchone()
            n, first, last, t0, t1 = r[:5]
            tot = dict(zip(SUM_COLS, r[5:]))
            m = self.meta(db)
            if lo is not None:
                total_blocks = hi - lo + 1
                tip = db.execute("SELECT MAX(height) FROM blocks").fetchone()[0]
                if tip is not None and hi > tip:
                    total_blocks = max(0, tip - lo + 1)
            elif m.get("full_done") == "1":
                total_blocks = n
            else:
                h0, h1 = self._height_at(db, start, after=True), self._height_at(db, end, after=False)
                total_blocks = max(n, h1 - h0 + 1) if h0 is not None and h1 is not None else n
        spam = sum(tot[k] for k in mr.CARRIERS)
        size = tot["size"]
        txs = tot["tx_count"] or 1
        return {
            "empty": n == 0, "blocks_scanned": n, "blocks_total": total_blocks,
            "coverage_pct": n / total_blocks * 100 if total_blocks else 0,
            "first": first, "last": last, "first_time": t0, "last_time": t1,
            "totals": tot, "spam_bytes": spam, "spam_pct": spam / size * 100 if size else 0,
            "saved_bytes": size - tot["stored"], "saved_pct": (size - tot["stored"]) / size * 100 if size else 0,
            "modified_tx_pct": (tot["modified"] + tot["stripped"]) / txs * 100,
            "by_carrier": {k: tot[k] for k in mr.CARRIERS},
        }

    def _height_at(self, db, t, after):
        """Approximate height of the first block at/after (or last block at/before) time t,
        interpolated between the nearest scanned blocks (blocks arrive at a nearly constant rate,
        so this is close even across the not-yet-scanned part of the history)."""
        below = db.execute("SELECT height, time FROM blocks WHERE time<=? ORDER BY time DESC LIMIT 1",
                           (t,)).fetchone()
        above = db.execute("SELECT height, time FROM blocks WHERE time>=? ORDER BY time LIMIT 1", (t,)).fetchone()
        if below is None and above is None:
            return None
        if below is None:
            # t lies before every scanned block: extrapolate back at the 10-minute target spacing
            # (not the earliest scanned height, which would make the range look fully scanned)
            return max(0, above[0] - int((above[1] - t) // BLOCK_SPACING))
        if above is None:
            return below[0] + (0 if not after else 1)
        if above[1] == below[1]:
            return below[0]
        frac = (t - below[1]) / (above[1] - below[1])
        h = below[0] + frac * (above[0] - below[0])
        return int(math.ceil(h)) if after else int(math.floor(h))

    def range(self, lo, hi, points=400):
        return self.cached(("range", lo, hi, points), 3, lambda: self._range(lo, hi, points))

    def _range(self, lo, hi, points=400):
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

    # ------------------------------------------------------------------ trends
    def trends(self):
        return self.cached("trends", self.ttl(30, 120), self._trends)

    def _trends(self):
        db = self.db()
        if db is None:
            return {"empty": True}
        with self.snapshot(db):
            return self._trends_in(db)

    def _trends_in(self, db):
        """
        Pace and records from the blocks scanned so far: spam of the last ~30 days (4,320
        blocks) and its yearly pace, the spam share of the last 1,000 blocks against the same
        window a year earlier, and the biggest spam block and month. A window counts only
        when at least 95 % of its blocks are scanned, so a half-scanned window is never
        presented as a trend; "pending" says from which block on a window will count.
        """
        hi = db.execute("SELECT MAX(height) FROM blocks").fetchone()[0]
        if hi is None:
            return {"empty": True}
        spam = "+".join(mr.CARRIERS)

        # The full scan runs from genesis upwards, so a window still short of 95 % fills up
        # when the scan's cursor passes its missing blocks: "at" is the block from which it counts
        # (None if those blocks will not come, e.g. on a pruned node or after the full scan).
        meta = self.meta(db)
        cursor = None if meta.get("full_done") == "1" else int(meta.get("full_next_height") or 0)
        pending = {}

        def window(name, lo, top):
            if lo < 0:
                pending[name] = None            # before the genesis block: never available
                return None
            r = db.execute(f"SELECT COUNT(*), COALESCE(SUM({spam}),0), COALESCE(SUM(size),0) "
                           "FROM blocks WHERE height BETWEEN ? AND ?", (lo, top)).fetchone()
            want = top - lo + 1
            if r[0] >= want * 0.95 and r[2]:
                return {"from": lo, "to": top, "blocks": r[0], "spam_bytes": r[1], "size": r[2],
                        "spam_pct": r[1] / r[2] * 100}
            at = None
            if cursor is not None:
                need = math.ceil(want * 0.95) - r[0]
                have = {h for (h,) in db.execute("SELECT height FROM blocks WHERE height BETWEEN ? AND ?",
                                                 (max(lo, cursor), top))}
                for hgt in range(max(lo, cursor), top + 1):
                    if hgt not in have:
                        need -= 1
                        if need <= 0:
                            at = hgt
                            break
            pending[name] = {"coverage": min(100.0, r[0] / want * 100), "at": at}
            return None

        month = window("last_month", hi - 4319, hi)
        recent = window("recent", hi - 999, hi)
        year_ago = window("year_ago", hi - BLOCKS_PER_YEAR - 999, hi - BLOCKS_PER_YEAR)
        b = db.execute(f"SELECT height, time, size, {spam} AS s FROM blocks ORDER BY s DESC, height LIMIT 1").fetchone()
        months = self.cached("history", self.ttl(30, 120), lambda: self._history_in(db))["months"]
        worst = max(months, key=lambda mo: sum(mo[k] for k in mr.CARRIERS), default=None)
        return {
            "empty": False,
            "complete": meta.get("full_done") == "1",
            "last_month": dict(month, per_year_bytes=month["spam_bytes"] * 365 / 30) if month else None,
            "recent": recent, "year_ago": year_ago,
            "pending": pending,
            "top_block": {"height": b[0], "time": b[1], "spam_bytes": b[3],
                          "spam_pct": b[3] / b[2] * 100 if b[2] else 0} if b and b[3] else None,
            "top_month": {"month": worst["month"], "spam_bytes": sum(worst[k] for k in mr.CARRIERS),
                          "blocks": worst["blocks"]} if worst and any(worst[k] for k in mr.CARRIERS) else None,
        }

    def history(self):
        return self.cached("history", self.ttl(30, 120), self._history)

    def _history(self):
        db = self.db()
        if db is None:
            return {"months": []}
        with self.snapshot(db):
            return self._history_in(db)

    def _history_in(self, db):
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
        return {"months": months, "coverage": self._coverage(db, self.meta(db))}

    # ------------------------------------------------------------------ export
    def iter_csv(self):
        """CSV export, row by row."""
        cols = [c for c in BLOCK_COLS if c not in ("height", "hash", "time")]
        buf = io.StringIO()
        w = csv.writer(buf)

        def take():
            v = buf.getvalue()
            buf.seek(0)
            buf.truncate()
            return v
        w.writerow(["height", "hash", "time_utc", *cols])
        yield take()
        db = self.db()
        if db is None:
            return
        cur = db.execute(f"SELECT {','.join(BLOCK_COLS)} FROM blocks ORDER BY height")
        for r in cur:
            d = _block_dict(r)
            w.writerow([csv_safe(d["height"]), csv_safe(d["hash"]),
                        csv_safe(time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(d["time"]))),
                        *[csv_safe(d[c]) for c in cols]])
            yield take()

    def iter_json(self):
        """JSON export {"summary": ..., "blocks": [...]}, streamed block by block."""
        yield '{"summary":' + json.dumps(self._summary(), default=str) + ',"blocks":['
        db = self.db()
        if db is not None:
            first = True
            for r in db.execute(f"SELECT {','.join(BLOCK_COLS)} FROM blocks ORDER BY height"):
                yield ("" if first else ",") + json.dumps(_block_dict(r), separators=(",", ":"))
                first = False
        yield "]}"


def _block_dict(r):
    d = dict(zip(BLOCK_COLS, r))
    d["hash"] = mr.hash_to_hex(bytes(d["hash"]))
    spam = sum(d[k] or 0 for k in mr.CARRIERS)
    d["spam_bytes"] = spam
    d["spam_pct"] = spam / d["size"] * 100 if d["size"] else 0
    d["utxo_done"] = bool(d["utxo_done"])
    return d


def block_from_result(height, res):
    """Same shape as an API block row, for a block analysed on demand (not stored)."""
    header = res["header"]
    d = {"height": height, "hash": mr.hash_to_hex(mr.block_hash(header)), "time": mr.header_time(header),
         "size": res["original"], "stored": res["stored"], "weight": res["weight"], "tx_count": res["tx_count"],
         "whole": res["whole"], "modified": res["modified"], "stripped": res["stripped"],
         "dust_outputs": res["dust_outputs"], "filter_entries": res["filter_entries"],
         "retained_protocol": res["retained_protocol"], "utxo_added": 0, "utxo_added_bytes": 0,
         "utxo_spent": 0, "utxo_spent_bytes": 0, "utxo_done": False, "on_demand": True}
    for k in mr.CARRIERS:
        d[k] = res[k]
    spam = sum(res[k] for k in mr.CARRIERS)
    d["spam_bytes"] = spam
    d["spam_pct"] = spam / d["size"] * 100 if d["size"] else 0
    return d


def _public_meta(m):
    keep = ("full_next_height", "full_start_height", "full_done", "quick_done", "sample_done", "utxo_complete",
            "rules_id")
    return {k: m.get(k) for k in keep}
