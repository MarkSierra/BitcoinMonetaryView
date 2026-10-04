# SPDX-License-Identifier: AGPL-3.0-or-later
"""
SQLite storage for per-block results and the spam UTXO set.

Only the app's own data directory is ever written. One database per network.

Spam UTXO tracking:
  spam_utxo   one row per spam/dust output currently unspent (compact: no script)
  utxo_undo   journal of adds/spends for the last REORG_WINDOW blocks, so blocks
              can be disconnected exactly
  utxo_hist   counts/bytes per (1,000-block bucket, kind) -> age bands, totals
  blocks      per-block results incl. UTXO deltas -> history over time
"""

import os
import sqlite3
import threading
import time

from .rules import monetary_rules as mr

SCHEMA_VERSION = 1
REORG_WINDOW = 288
BUCKET = 1000

BLOCK_COLS = ("height", "hash", "time", "size", "stored", "weight", "tx_count",
              "envelope", "op_return", "multisig", "scriptsig",
              "whole", "modified", "stripped", "dust_outputs", "filter_entries",
              "retained_protocol", "utxo_added", "utxo_added_bytes",
              "utxo_spent", "utxo_spent_bytes", "utxo_done")

DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS blocks (
    height INTEGER PRIMARY KEY, hash BLOB NOT NULL, time INTEGER, size INTEGER, stored INTEGER,
    weight INTEGER, tx_count INTEGER, envelope INTEGER, op_return INTEGER, multisig INTEGER,
    scriptsig INTEGER, whole INTEGER, modified INTEGER, stripped INTEGER, dust_outputs INTEGER,
    filter_entries INTEGER, retained_protocol INTEGER, utxo_added INTEGER, utxo_added_bytes INTEGER,
    utxo_spent INTEGER, utxo_spent_bytes INTEGER, utxo_done INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS spam_utxo (
    txid BLOB NOT NULL, vout INTEGER NOT NULL, height INTEGER NOT NULL, value INTEGER NOT NULL,
    kind INTEGER NOT NULL, script_len INTEGER NOT NULL, PRIMARY KEY (txid, vout)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS utxo_undo (
    height INTEGER NOT NULL, seq INTEGER NOT NULL, op INTEGER NOT NULL, txid BLOB NOT NULL,
    vout INTEGER NOT NULL, created INTEGER NOT NULL, value INTEGER NOT NULL, kind INTEGER NOT NULL,
    script_len INTEGER NOT NULL, PRIMARY KEY (height, seq)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS utxo_hist (
    bucket INTEGER NOT NULL, kind INTEGER NOT NULL, count INTEGER NOT NULL, bytes INTEGER NOT NULL,
    PRIMARY KEY (bucket, kind)
) WITHOUT ROWID;
"""

OP_ADD, OP_SPEND = 1, 2


class Bloom:
    """
    Bloom filter over outpoints. Txids are uniformly random, so bit indices are
    taken straight from txid bytes (mixed with vout) instead of re-hashing.
    False positives only cost one SQLite lookup; false negatives cannot occur.
    """

    K = 7

    def __init__(self, mbytes):
        bits = max(1 << 16, int(mbytes) * 8 * 1024 * 1024)
        self.bits = 1 << (bits.bit_length() - 1)        # power of two -> mask
        self.mask = self.bits - 1
        self.arr = bytearray(self.bits >> 3)
        self.count = 0

    def _idx(self, txid, vout):
        # One big-int conversion, then K 32-bit windows (txids are uniformly random).
        x = int.from_bytes(txid[:28], "little") ^ ((vout + 1) * 0x9E3779B97F4A7C15)
        m = self.mask
        return [(x >> (32 * i)) & m for i in range(self.K)]

    def add(self, txid, vout):
        a = self.arr
        for i in self._idx(txid, vout):
            a[i >> 3] |= 1 << (i & 7)
        self.count += 1

    def maybe(self, txid, vout):
        a = self.arr
        for i in self._idx(txid, vout):
            if not a[i >> 3] & (1 << (i & 7)):
                return False
        return True


class Store:
    def __init__(self, path, readonly=False, cache_mb=64):
        self.path = path
        self.readonly = readonly
        if readonly:
            uri = "file:" + path + "?mode=ro"
            self.db = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=30)
        else:
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            self.db = sqlite3.connect(path, check_same_thread=False, timeout=30, isolation_level=None)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(DDL)
            if self.get_meta("schema_version") is None:
                self.set_meta("schema_version", SCHEMA_VERSION)
        self.db.execute(f"PRAGMA cache_size=-{int(cache_mb) * 1024}")
        self.lock = threading.RLock()
        self._seq = 0
        self.in_tx = False

    # ----------------------------------------------------------------- meta
    def get_meta(self, key, default=None):
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return default if r is None else r[0]

    def set_meta(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, str(value)))

    # ----------------------------------------------------------------- transactions
    def begin(self):
        if not self.in_tx:
            self.db.execute("BEGIN IMMEDIATE")
            self.in_tx = True

    def commit(self):
        if self.in_tx:
            self.db.execute("COMMIT")
            self.in_tx = False

    def rollback(self):
        if self.in_tx:
            self.db.execute("ROLLBACK")
            self.in_tx = False

    # ----------------------------------------------------------------- blocks
    def block_hash_at(self, height):
        r = self.db.execute("SELECT hash FROM blocks WHERE height=?", (height,)).fetchone()
        return None if r is None else bytes(r[0])

    def block_row(self, height):
        cur = self.db.execute(f"SELECT {','.join(BLOCK_COLS)} FROM blocks WHERE height=?", (height,))
        r = cur.fetchone()
        return None if r is None else dict(zip(BLOCK_COLS, r))

    def has_any_blocks(self):
        return self.db.execute("SELECT 1 FROM blocks LIMIT 1").fetchone() is not None

    def max_height(self):
        r = self.db.execute("SELECT MAX(height) FROM blocks").fetchone()
        return r[0]

    def recent_hashes(self, at_or_below, limit):
        rows = self.db.execute("SELECT height, hash FROM blocks WHERE height<=? ORDER BY height DESC LIMIT ?",
                               (at_or_below, limit)).fetchall()
        return [(h, bytes(b)) for h, b in rows]

    def processed_bytes(self):
        r = self.db.execute("SELECT COALESCE(SUM(size),0) FROM blocks WHERE utxo_done=1").fetchone()
        return r[0]

    def has_block(self, height):
        return self.db.execute("SELECT 1 FROM blocks WHERE height=?", (height,)).fetchone() is not None

    def put_block(self, height, res, utxo_done, utxo_delta=(0, 0, 0, 0)):
        header = res["header"]
        row = (height, mr.block_hash(header), mr.header_time(header), res["original"], res["stored"],
               res["weight"], res["tx_count"], res["envelope"], res["op_return"], res["multisig"],
               res["scriptsig"], res["whole"], res["modified"], res["stripped"], res["dust_outputs"],
               res["filter_entries"], res["retained_protocol"], *utxo_delta, 1 if utxo_done else 0)
        self.db.execute(f"INSERT OR REPLACE INTO blocks({','.join(BLOCK_COLS)}) "
                        f"VALUES({','.join('?' * len(BLOCK_COLS))})", row)

    # ----------------------------------------------------------------- spam UTXO
    def _hist(self, height, kind, dcount, dbytes):
        self.db.execute(
            "INSERT INTO utxo_hist(bucket, kind, count, bytes) VALUES(?,?,?,?) "
            "ON CONFLICT(bucket, kind) DO UPDATE SET count=count+excluded.count, bytes=bytes+excluded.bytes",
            (height // BUCKET, kind, dcount, dbytes))

    def apply_block_utxo(self, height, res, bloom):
        """
        Apply a block's spends and spam outputs in transaction order semantics:
        spends first is safe because an output can only be spent by a later tx
        (within the block, created outputs are added before later spends).
        Returns (added, added_bytes, spent, spent_bytes).
        """
        db = self.db
        added = added_b = spent = spent_b = 0
        seq = 0
        # Add this block's spam outputs first, so in-block spends find them.
        for txid, vout, value, kind, slen in res["utxo_spam"]:
            cb = mr.coin_bytes(slen)
            db.execute("INSERT OR REPLACE INTO spam_utxo(txid, vout, height, value, kind, script_len) "
                       "VALUES(?,?,?,?,?,?)", (txid, vout, height, value, kind, slen))
            db.execute("INSERT INTO utxo_undo VALUES(?,?,?,?,?,?,?,?,?)",
                       (height, seq, OP_ADD, txid, vout, height, value, kind, slen))
            seq += 1
            self._hist(height, kind, 1, cb)
            bloom.add(txid, vout)
            added += 1
            added_b += cb
        for prev, vout in res["spends"]:
            if not bloom.maybe(prev, vout):
                continue
            r = db.execute("SELECT height, value, kind, script_len FROM spam_utxo WHERE txid=? AND vout=?",
                           (prev, vout)).fetchone()
            if r is None:
                continue
            created, value, kind, slen = r
            db.execute("DELETE FROM spam_utxo WHERE txid=? AND vout=?", (prev, vout))
            db.execute("INSERT INTO utxo_undo VALUES(?,?,?,?,?,?,?,?,?)",
                       (height, seq, OP_SPEND, prev, vout, created, value, kind, slen))
            seq += 1
            cb = mr.coin_bytes(slen)
            self._hist(created, kind, -1, -cb)
            spent += 1
            spent_b += cb
        return added, added_b, spent, spent_b

    def disconnect_block(self, height, bloom=None):
        """Undo a block's UTXO changes (reverse order) and remove its row.

        Restored outputs are re-added to the Bloom filter: it may have been rebuilt
        since they were spent, and a missing entry would hide a later spend."""
        db = self.db
        rows = db.execute("SELECT op, txid, vout, created, value, kind, script_len FROM utxo_undo "
                          "WHERE height=? ORDER BY seq DESC", (height,)).fetchall()
        row = self.block_row(height)
        if row and row["utxo_done"] and not rows and (row["utxo_added"] or row["utxo_spent"]):
            raise RuntimeError(f"cannot disconnect block {height}: undo data no longer available")
        for op, txid, vout, created, value, kind, slen in rows:
            cb = mr.coin_bytes(slen)
            if op == OP_ADD:
                db.execute("DELETE FROM spam_utxo WHERE txid=? AND vout=?", (txid, vout))
                self._hist(created, kind, -1, -cb)
            else:
                db.execute("INSERT OR REPLACE INTO spam_utxo VALUES(?,?,?,?,?,?)",
                           (txid, vout, created, value, kind, slen))
                self._hist(created, kind, 1, cb)
                if bloom is not None:
                    bloom.add(bytes(txid), vout)
        db.execute("DELETE FROM utxo_undo WHERE height=?", (height,))
        db.execute("DELETE FROM blocks WHERE height=?", (height,))

    def prune_undo(self, below_height):
        self.db.execute("DELETE FROM utxo_undo WHERE height < ?", (below_height,))

    def iter_spam_outpoints(self, batch=100_000):
        cur = self.db.execute("SELECT txid, vout FROM spam_utxo")
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                return
            yield from rows

    def spam_utxo_count(self):
        r = self.db.execute("SELECT COALESCE(SUM(count),0) FROM utxo_hist").fetchone()
        return r[0]

    def reset(self):
        """Delete all analysis results (e.g. after a rules change). Settings are untouched."""
        self.begin()
        for t in ("blocks", "spam_utxo", "utxo_undo", "utxo_hist"):
            self.db.execute(f"DELETE FROM {t}")
        for k in ("full_next_height", "full_start_height", "full_done", "quick_done", "quick_top",
                  "utxo_complete", "utxo_info", "utxo_info_time"):
            self.db.execute("DELETE FROM meta WHERE key=?", (k,))
        self.commit()

    def size_on_disk(self):
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += os.path.getsize(self.path + suffix)
            except OSError:
                pass
        return total

    def close(self):
        try:
            self.commit()
        finally:
            self.db.close()


def now():
    return time.time()
