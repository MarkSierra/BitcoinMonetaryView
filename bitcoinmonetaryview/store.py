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
  sample      per-block results of an evenly spaced sample of not-yet-scanned history,
              used only for the early whole-chain estimate (never mixed into `blocks`)
"""

import hashlib
import os
import sqlite3
import struct
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
CREATE INDEX IF NOT EXISTS blocks_time ON blocks(time);
CREATE INDEX IF NOT EXISTS blocks_hash ON blocks(hash);
CREATE TABLE IF NOT EXISTS sample (
    height INTEGER PRIMARY KEY, hash BLOB NOT NULL, size INTEGER, stored INTEGER, weight INTEGER,
    tx_count INTEGER, envelope INTEGER, op_return INTEGER, multisig INTEGER, scriptsig INTEGER,
    whole INTEGER, modified INTEGER, stripped INTEGER, dust_outputs INTEGER, filter_entries INTEGER,
    retained_protocol INTEGER
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
    Blocked Bloom filter over outpoints: all K bits of an entry lie in one 512-bit
    (64-byte) block, so adding or checking an entry touches one cache line instead of
    K random places in a large array. Txids are uniformly random, so the block and the
    bit positions are taken straight from txid bytes (mixed with vout) instead of
    re-hashing. False positives only cost one SQLite lookup; false negatives cannot occur.
    """

    K = 7
    _MIX = 0x9E3779B97F4A7C15

    def __init__(self, mbytes):
        self._size(mbytes)
        self.arr = bytearray(self.bits >> 3)
        self.count = 0

    def _size(self, mbytes):
        bits = max(1 << 16, int(mbytes) * 8 * 1024 * 1024)
        self.bits = 1 << (bits.bit_length() - 1)        # power of two -> masks
        self.bshift = (self.bits >> 9).bit_length() - 1  # bits that select the block
        self.bmask = (1 << self.bshift) - 1

    def add(self, txid, vout):
        x = int.from_bytes(txid[:28], "little") ^ ((vout + 1) * self._MIX)
        b = (x & self.bmask) << 6                        # byte offset of the entry's block
        x >>= self.bshift
        a = self.arr
        for _ in range(self.K):                          # K 9-bit positions within the block
            i = x & 511
            a[b | (i >> 3)] |= 1 << (i & 7)
            x >>= 9
        self.count += 1

    def add_many(self, outpoints):
        """add() for many (txid, vout) pairs; the same bits, about twice as fast."""
        assert self.K == 7
        a, bm, bs, mix, fb = self.arr, self.bmask, self.bshift, self._MIX, int.from_bytes
        n = 0
        for txid, vout in outpoints:
            x = fb(txid[:28], "little") ^ ((vout + 1) * mix)
            b = (x & bm) << 6
            x >>= bs
            i = x & 511; a[b | (i >> 3)] |= 1 << (i & 7)
            i = (x >> 9) & 511; a[b | (i >> 3)] |= 1 << (i & 7)
            i = (x >> 18) & 511; a[b | (i >> 3)] |= 1 << (i & 7)
            i = (x >> 27) & 511; a[b | (i >> 3)] |= 1 << (i & 7)
            i = (x >> 36) & 511; a[b | (i >> 3)] |= 1 << (i & 7)
            i = (x >> 45) & 511; a[b | (i >> 3)] |= 1 << (i & 7)
            i = (x >> 54) & 511; a[b | (i >> 3)] |= 1 << (i & 7)
            n += 1
        self.count += n

    def maybe(self, txid, vout):
        x = int.from_bytes(txid[:28], "little") ^ ((vout + 1) * self._MIX)
        b = (x & self.bmask) << 6
        x >>= self.bshift
        a = self.arr
        for _ in range(self.K):
            i = x & 511
            if not a[b | (i >> 3)] & (1 << (i & 7)):
                return False
            x >>= 9
        return True

    # ------------------------------------------------------------- saved copy
    # A saved filter lets a restart skip the rebuild from the database. The file
    # carries a random token that Store.save_bloom also records in the database
    # right after writing it, and Store.take_saved_bloom erases before anything can
    # change the database again: the file is only used while both tokens match,
    # i.e. while the database is unchanged since the save.
    # Bump FILE_VERSION whenever the bit layout (_pos, add_many) or K changes.
    FILE_MAGIC = b"BMVSPENT"
    FILE_VERSION = 2                               # 2: blocked layout (0.2.4)
    _HEADER = struct.Struct("<8sIIQQ16s32s")       # magic, version, K, bits, count, token, digest

    def save(self, path, token):
        """Write the filter atomically (temporary file, fsync, rename)."""
        digest = hashlib.blake2b(self.arr, digest_size=32).digest()
        header = self._HEADER.pack(self.FILE_MAGIC, self.FILE_VERSION, self.K, self.bits, self.count,
                                   token, digest)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(header)
                f.write(self.arr)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        try:
            dfd = os.open(os.path.dirname(path) or ".", os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass

    @classmethod
    def load(cls, path, mbytes, token):
        """The saved filter if it is intact, has this size and carries `token`; else None."""
        b = cls.__new__(cls)
        b._size(mbytes)
        try:
            with open(path, "rb") as f:
                head = f.read(cls._HEADER.size)
                if len(head) != cls._HEADER.size:
                    return None
                magic, ver, k, bits, count, tok, digest = cls._HEADER.unpack(head)
                if (magic, ver, k, bits, tok) != (cls.FILE_MAGIC, cls.FILE_VERSION, cls.K, b.bits, token):
                    return None
                if os.fstat(f.fileno()).st_size != cls._HEADER.size + (b.bits >> 3):
                    return None
                b.arr = bytearray(b.bits >> 3)
                if f.readinto(b.arr) != len(b.arr):
                    return None
        except OSError:
            return None
        if hashlib.blake2b(b.arr, digest_size=32).digest() != digest:
            return None
        b.count = count
        return b


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
        """True if any results (exact or sample) exist."""
        return (self.db.execute("SELECT 1 FROM blocks LIMIT 1").fetchone() is not None
                or self.db.execute("SELECT 1 FROM sample LIMIT 1").fetchone() is not None)

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

    def lowest_block_from(self, height):
        """Lowest stored block height >= height (None if there is none)."""
        return self.db.execute("SELECT MIN(height) FROM blocks WHERE height>=?", (height,)).fetchone()[0]

    # ----------------------------------------------------------------- sample
    def has_sample(self, height):
        return self.db.execute("SELECT 1 FROM sample WHERE height=?", (height,)).fetchone() is not None

    def put_sample(self, height, res):
        row = (height, mr.block_hash(res["header"]), res["original"], res["stored"], res["weight"],
               res["tx_count"], res["envelope"], res["op_return"], res["multisig"], res["scriptsig"],
               res["whole"], res["modified"], res["stripped"], res["dust_outputs"], res["filter_entries"],
               res["retained_protocol"])
        self.db.execute(f"INSERT OR REPLACE INTO sample VALUES({','.join('?' * len(row))})", row)

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

    # ----------------------------------------------------------------- saved spent-output filter
    def bloom_path(self):
        return os.path.join(os.path.dirname(self.path), "spent-filter.bin")

    def take_saved_bloom(self, mbytes):
        """The filter saved by the last clean stop if it still matches this database, else None.
        Either way the saved copy is invalidated first, before anything can change the
        database again; after a crash from here on, the next start rebuilds."""
        token = self.get_meta("bloom_token")
        saved = None
        if token:
            try:
                saved = Bloom.load(self.bloom_path(), mbytes, bytes.fromhex(token))
            except ValueError:
                saved = None
        self.discard_saved_bloom()
        return saved

    def discard_saved_bloom(self):
        self.db.execute("DELETE FROM meta WHERE key='bloom_token'")
        for p in (self.bloom_path(), self.bloom_path() + ".tmp"):    # .tmp: a save cut short
            try:
                os.unlink(p)
            except OSError:
                pass

    def save_bloom(self, bloom):
        """Save the filter; the token goes into the database only after the file is complete."""
        token = os.urandom(16)
        bloom.save(self.bloom_path(), token)
        self.set_meta("bloom_token", token.hex())

    def iter_spam_outpoint_batches(self, batch=50_000):
        """All (txid, vout) of the spam UTXO table, in lists of up to `batch` rows."""
        cur = self.db.execute("SELECT txid, vout FROM spam_utxo")
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                return
            yield rows

    def spam_utxo_count(self):
        r = self.db.execute("SELECT COALESCE(SUM(count),0) FROM utxo_hist").fetchone()
        return r[0]

    def reset(self):
        """Delete all analysis results (e.g. after a rules change). Settings are untouched."""
        self.begin()
        for t in ("blocks", "sample", "spam_utxo", "utxo_undo", "utxo_hist"):
            self.db.execute(f"DELETE FROM {t}")
        for k in ("full_next_height", "full_start_height", "full_done", "quick_done", "quick_top",
                  "utxo_complete", "utxo_info", "utxo_info_time", "sample_done", "sample_top",
                  "sample_every"):
            self.db.execute("DELETE FROM meta WHERE key=?", (k,))
        self.discard_saved_bloom()
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
