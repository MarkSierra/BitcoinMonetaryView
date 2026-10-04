# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Background scanner: quick pass, full-history pass, live following.

Everything it does to the node is a read through node.client.Node, which only
permits whitelisted read-only RPCs. Everything it writes goes to the app's own
SQLite database.
"""

import collections
import concurrent.futures
import multiprocessing
import datetime
import logging
import os
import threading
import time

from . import __version__
from .node.client import Node, default_dust_start, network_name
from .node.rpc import RPC_IN_WARMUP, NodeAuthError, NodeError, RPCError, RPCClient
from .node.transport import NodeUnreachable
from .rules import UPSTREAM_COMMIT
from .rules import monetary_rules as mr
from .rules.upstream import carrier_policy as cp
from .store import REORG_WINDOW, Bloom, Store
from . import worker

log = logging.getLogger("bmv.scanner")

APP_RULES_REVISION = 1
CONNECTION_KEYS = ("rpc_url", "rpc_user", "rpc_password", "rpc_cookie_file", "rpc_cafile",
                   "tor_proxy", "use_rest", "rpc_timeout")
RULES_KEYS = ("carrier_policy", "dust_start_height")

PROFILES = {
    # workers: parallel block requests; pause: sleep after each block as a multiple of its fetch time
    # procs: analysis worker processes (CPU cores used for parsing; one core is always left free)
    "eco": {"workers": 1, "procs": 0, "pause": 1.0, "min_sleep": 0.05, "label": "Eco"},
    "balanced": {"workers": 2, "procs": 2, "pause": 0.25, "min_sleep": 0.0, "label": "Balanced"},
    "full": {"workers": 4, "procs": 4, "pause": 0.0, "min_sleep": 0.0, "label": "Full speed"},
}

PHASE_LABELS = {
    "starting": "Starting",
    "connecting": "Connecting",
    "waiting_node": "Waiting for node",
    "loading_filter": "Loading spent-output filter",
    "quick": "Quick pass",
    "full": "Full history scan",
    "live": "Live",
    "paused": "Paused",
    "window": "Waiting for scan window",
    "utxo_info": "Reading UTXO set size",
    "error": "Error",
}

LIVE_POLL_SECONDS = 5
UTXO_INFO_INTERVAL = 24 * 3600
COMMIT_SECONDS = 2.0
COMMIT_BLOCKS = 200


def in_window(window, tz, now=None):
    """True if `now` (aware datetime) lies in a daily HH:MM-HH:MM window (may cross midnight)."""
    if not window:
        return True
    from zoneinfo import ZoneInfo
    now = now or datetime.datetime.now(ZoneInfo(tz))
    a, b = window.split("-")
    start = int(a[:2]) * 60 + int(a[3:])
    end = int(b[:2]) * 60 + int(b[3:])
    cur = now.hour * 60 + now.minute
    if start == end:
        return True
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end


class Status:
    """Thread-safe scanner status for the API and logs."""

    def __init__(self):
        self.lock = threading.Lock()
        self.data = {"phase": "starting", "phase_label": PHASE_LABELS["starting"], "detail": "",
                     "version": __version__, "started_at": time.time()}
        self.activity = collections.deque(maxlen=50)

    def set(self, **kw):
        with self.lock:
            if "phase" in kw:
                kw["phase_label"] = PHASE_LABELS.get(kw["phase"], kw["phase"])
            self.data.update(kw)

    def get(self, key, default=None):
        with self.lock:
            return self.data.get(key, default)

    def event(self, level, message):
        with self.lock:
            self.activity.appendleft({"time": time.time(), "level": level, "message": message})
        getattr(log, level if level in ("info", "warning", "error") else "info")(message)

    def snapshot(self):
        with self.lock:
            d = dict(self.data)
            d["activity"] = list(self.activity)
            d["now"] = time.time()
            return d


class RateMeter:
    """Blocks/s and bytes/s over a sliding window."""

    def __init__(self, seconds=60):
        self.seconds = seconds
        self.samples = collections.deque()

    def add(self, nbytes, t=None):
        t = t or time.monotonic()
        self.samples.append((t, nbytes))
        while self.samples and self.samples[0][0] < t - self.seconds:
            self.samples.popleft()

    def rates(self):
        if len(self.samples) < 2:
            return 0.0, 0.0
        span = self.samples[-1][0] - self.samples[0][0]
        if span <= 0:
            return 0.0, 0.0
        n = len(self.samples) - 1
        b = sum(s[1] for s in list(self.samples)[1:])
        return n / span, b / span


class Restart(Exception):
    """Settings changed in a way that needs a new session."""


class Scanner(threading.Thread):
    def __init__(self, config, status=None):
        super().__init__(name="scanner", daemon=True)
        self.config = config
        self.status = status or Status()
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.paused = False
        self.rescan_requested = False
        self.store = None
        self.store_path = None
        self.network = None
        self.node = None
        self.bloom = None
        self.meter = RateMeter()
        self.latency_ewma = None
        self.latency_floor = None
        self._conn_sig = None
        self._rules_sig = None
        self.processed_bytes = 0
        self.total_bytes_estimate = None
        self._pool = None
        self._pool_workers = None
        self._apool = None
        self._apool_key = None
        self.status.set(paused=False)

    # ------------------------------------------------------------- control
    def stop(self):
        self.stop_event.set()
        self.wake.set()

    def pause(self):
        self.paused = True
        self.status.set(paused=True)
        self.status.event("info", "Scanning paused by user")
        self.wake.set()

    def resume(self):
        self.paused = False
        self.status.set(paused=False)
        self.status.event("info", "Scanning resumed")
        self.wake.set()

    def reset_results(self, message, level="warning"):
        """Delete this app's results and every in-memory value derived from them."""
        self.store.reset()
        self.bloom = None                       # free the old filter before allocating a new one
        self.bloom = Bloom(self.config.bloom_mb)
        self.processed_bytes = 0
        self.status.set(full_started_at=None)
        self.status.event(level, message)

    def request_rescan(self):
        self.rescan_requested = True
        self.wake.set()

    def sleep(self, seconds):
        self.wake.wait(seconds)
        self.wake.clear()
        return not self.stop_event.is_set()

    # ------------------------------------------------------------- helpers
    def _sig(self, keys):
        return tuple(self.config.get(k) for k in keys)

    def check_config(self):
        if self.config.file_changed():
            self.config.load()
            self.status.event("info", "Settings file changed; applied")
            logging.getLogger().setLevel(self.config.log_level.upper())
        if self._sig(CONNECTION_KEYS) != self._conn_sig:
            raise Restart("connection settings changed")
        if self._sig(RULES_KEYS) != self._rules_sig:
            raise Restart("rule settings changed")
        if self.rescan_requested:
            raise Restart("rescan requested")
        self.status.set(profile=self.config.speed_profile, scan_window=self.config.scan_window,
                        timezone=self.config.timezone)

    def make_node(self):
        c = self.config
        proxy = None
        if c.tor_proxy:
            host, port = c.tor_proxy.rsplit(":", 1)
            proxy = (host.strip("[]"), int(port))
        return Node(c.rpc_url, user=c.rpc_user or None, password=c.rpc_password,
                    cookie_file=c.rpc_cookie_file or None, proxy=proxy, cafile=c.rpc_cafile or None,
                    timeout=c.rpc_timeout, use_rest=c.use_rest)

    def policy(self):
        return cp.Policy() if self.config.carrier_policy else None

    def dust_start(self):
        v = self.config.dust_start_height
        return default_dust_start(self.network) if v is None else v

    def rules_id(self):
        pol = self.policy()
        return (f"app:{APP_RULES_REVISION};upstream:{UPSTREAM_COMMIT[:12]};"
                f"policy:{pol.policy_id()[:16] if pol else 'off'};dust:{self.dust_start()};"
                f"opr:{mr.DEFAULT_OP_RETURN_LIMIT};ss:{mr.DEFAULT_SCRIPTSIG_LIMIT}")

    # ------------------------------------------------------------- main loop
    def run(self):
        backoff = 2
        while not self.stop_event.is_set():
            try:
                self.session()
                backoff = 2
            except Restart as r:
                self.status.event("info", f"Restarting scanner: {r}")
                backoff = 2
                continue
            except NodeAuthError as e:
                self.status.set(phase="error", detail=str(e), last_error=str(e))
                self.status.event("error", f"Authentication problem: {e}")
                self.sleep(30)
            except NodeUnreachable as e:
                self.status.set(phase="waiting_node", detail=str(e), last_error=str(e))
                self.status.event("warning", f"Node not reachable, retrying in {backoff}s: {e}")
                self.sleep(backoff)
                backoff = min(backoff * 2, 60)
            except RPCError as e:
                if e.code == RPC_IN_WARMUP:
                    self.status.set(phase="waiting_node", detail="Node is starting up (warming up)")
                    self.sleep(10)
                else:
                    self.status.set(phase="error", detail=str(e), last_error=str(e))
                    self.status.event("error", f"Node returned an error: {e}")
                    self.sleep(30)
            except NodeError as e:
                self.status.set(phase="error", detail=str(e), last_error=str(e))
                self.status.event("error", f"Node communication error: {e}")
                self.sleep(30)
            except mr.BlockInvalid as e:
                self.status.set(phase="error", detail=str(e), last_error=str(e))
                self.status.event("error", f"Block data check failed, will retry: {e}")
                self.sleep(60)
            except Exception as e:  # never let the thread die
                log.exception("scanner crashed")
                self.status.set(phase="error", detail=f"Internal error: {e}", last_error=str(e))
                self.status.event("error", f"Internal error, will retry: {e}")
                self.sleep(60)
            finally:
                if self.store is not None and self.store.in_tx:
                    try:
                        self.store.rollback()
                    except Exception:
                        pass
                self.shutdown_pool()
                self.shutdown_analysis_pool()
                if self.node is not None:
                    self.node.close()
        if self.store:
            self.store.close()

    def session(self):
        self.config.load() if self.config.file_changed() else None
        self._conn_sig = self._sig(CONNECTION_KEYS)
        self._rules_sig = self._sig(RULES_KEYS)
        self.status.set(phase="connecting", detail=f"Connecting to {self.config.rpc_url}")
        self.node = self.make_node()
        info = self.node.chain_info()
        net = network_name(info.get("chain"))
        rest = self.node.probe_rest()
        ninfo = {}
        try:
            ninfo = self.node.network_info()
        except NodeError:
            pass
        self.status.set(node={
            "network": net, "url": self.node.transport.describe(), "rest": rest,
            "version": ninfo.get("subversion", ""), "pruned": bool(info.get("pruned")),
            "prune_height": info.get("pruneheight"), "plaintext_remote": self.node.transport.plaintext_remote,
            "tip": info.get("blocks"), "size_on_disk": info.get("size_on_disk")})
        if self.node.transport.plaintext_remote:
            self.status.event("warning", "RPC runs over plain HTTP to another machine: your RPC password is sent "
                                         "unencrypted. Prefer https, Tor or an SSH tunnel.")
        self.status.event("info", f"Connected to {net} node {ninfo.get('subversion', '')} "
                                  f"(REST {'on' if rest else 'off'})")
        self.open_store(net)
        self._policy_obj = self.policy()
        rid = self.rules_id()
        if self.rescan_requested:
            self.rescan_requested = False
            self.reset_results("Rescan started: previous results deleted", "info")
        old = self.store.get_meta("rules_id")
        if old is not None and old != rid and self.store.has_any_blocks():
            self.reset_results("Spam rules changed; previous results were deleted and the chain "
                               "will be rescanned with the new rules")
        self.store.set_meta("rules_id", rid)
        self.status.set(rules={"id": rid, "upstream_commit": UPSTREAM_COMMIT,
                               "carrier_policy": bool(self._policy_obj),
                               "policy_id": self._policy_obj.policy_id() if self._policy_obj else None,
                               "dust_start_height": self.dust_start()})
        self.load_bloom()
        self.loop()

    def open_store(self, net):
        path = os.path.join(self.config.data_dir, net, "bmv.sqlite")
        if self.store_path != path:
            if self.store:
                self.store.close()
            self.store = Store(path)
            self.store_path = path
            self.network = net
            self.bloom = None
        self.status.set(network=net, db_path=path)

    def load_bloom(self):
        if self.bloom is not None:
            return
        self.status.set(phase="loading_filter", detail="Loading spent-output filter from the database")
        bloom = Bloom(self.config.bloom_mb)
        n = 0
        for txid, vout in self.store.iter_spam_outpoints():
            bloom.add(bytes(txid), vout)
            n += 1
            if n % 1_000_000 == 0:
                self.status.set(detail=f"Loading spent-output filter: {n:,} entries")
                if self.stop_event.is_set():
                    return
        self.bloom = bloom
        if n:
            self.status.event("info", f"Spent-output filter loaded ({n:,} spam outputs)")

    # ------------------------------------------------------------- scheduling
    def loop(self):
        last_info = 0
        info = None
        while not self.stop_event.is_set():
            self.check_config()
            if info is None or time.monotonic() - last_info > 30:
                info = self.node.chain_info()
                last_info = time.monotonic()
                self.update_node_info(info)
            if info.get("initialblockdownload"):
                pct = float(info.get("verificationprogress", 0)) * 100
                self.status.set(phase="waiting_node",
                                detail=f"Your node is still syncing ({pct:.1f} %). Scanning starts when it is done.")
                info = None
                self.sleep(60)
                continue
            tip = int(info["blocks"])
            self.handle_reorg(tip)
            full_next = self.full_next(info)
            history_pending = full_next <= tip
            if history_pending:
                if self.paused:
                    self.status.set(phase="paused", detail="Scanning is paused. New blocks are still shown.")
                    self.follow_new_blocks_quick(tip)
                    self.sleep(LIVE_POLL_SECONDS)
                    info = None
                    continue
                if not in_window(self.config.scan_window, self.config.timezone):
                    self.status.set(phase="window",
                                    detail=f"History scan runs daily {self.config.scan_window} "
                                           f"({self.config.timezone}). New blocks are still shown.")
                    self.follow_new_blocks_quick(tip)
                    self.sleep(30)
                    info = None
                    continue
                q = self.config.quick_pass_blocks
                if q and self.store.get_meta("quick_done") != "1" and tip - full_next + 1 > q:
                    self.quick_pass(tip, q)
                    self.maybe_utxo_info(force_first=True)
                else:
                    self.full_pass(tip, info)
                info = None if time.monotonic() - last_info > 30 else info
            else:
                self.status.set(phase="live", detail="Up to date. Waiting for the next block.",
                                height=tip, tip=tip, progress=100.0, eta_seconds=0)
                self.maybe_utxo_info()
                self.sleep(LIVE_POLL_SECONDS)
                best = self.node.best_hash()
                stored = self.store.block_hash_at(tip)
                if stored is None or mr.hash_to_hex(stored) != best:
                    info = None          # new block (or reorg) -> refresh

    def update_node_info(self, info):
        node = dict(self.status.get("node") or {})
        node.update({"tip": info.get("blocks"), "pruned": bool(info.get("pruned")),
                     "prune_height": info.get("pruneheight"), "size_on_disk": info.get("size_on_disk"),
                     "ibd": bool(info.get("initialblockdownload"))})
        self.status.set(node=node)
        sod = info.get("size_on_disk")
        if sod and not info.get("pruned"):
            # size_on_disk includes undo (rev*.dat) files, roughly 13 % on top of block data
            self.total_bytes_estimate = sod / 1.13

    def full_next(self, info):
        v = self.store.get_meta("full_next_height")
        if v is None:
            start = 0
            if info.get("pruned"):
                start = int(info.get("pruneheight") or 0)
            self.store.set_meta("full_start_height", start)
            self.store.set_meta("full_next_height", start)
            self.store.set_meta("utxo_complete", "1" if start == 0 else "0")
            if start:
                self.status.event("warning", f"Pruned node: history before block {start:,} is not available; "
                                             "spam UTXO figures will be incomplete")
            return start
        return int(v)

    # ------------------------------------------------------------- reorgs
    def handle_reorg(self, tip):
        top = self.store.max_height()
        if top is None:
            return
        # Walk back over our stored blocks (highest first) until one matches the
        # node's main chain. Blocks above the node's tip are disconnected too.
        rows = self.store.recent_hashes(min(top, tip), REORG_WINDOW + 10)
        fork = None
        for h, stored in rows:
            if mr.hash_to_hex(stored) == self.node.block_hash(h):
                fork = h
                break
        if fork is None:
            if rows:
                self.reset_results("Very deep chain reorganisation; restarting the scan from scratch")
                return
            fork = tip          # we only hold blocks above the node's tip
        if fork == top:
            return
        try:
            self.store.begin()
            for x in range(top, fork, -1):
                if self.store.has_block(x):
                    self.store.disconnect_block(x, self.bloom)
            qt = self.store.get_meta("quick_top")
            if qt is not None and int(qt) > fork:
                self.store.set_meta("quick_top", fork)
            nxt = int(self.store.get_meta("full_next_height", 0))
            if nxt > fork + 1:
                self.store.set_meta("full_next_height", fork + 1)
                self.processed_bytes = self.store.processed_bytes()
            self.store.commit()
        except RuntimeError as e:
            self.store.rollback()
            self.reset_results(f"Very deep reorganisation ({e}); restarting the scan from scratch")
            return
        self.status.event("warning", f"Chain reorganisation: blocks {fork + 1:,}–{top:,} were replaced; "
                                     "results rolled back and will be re-analysed")

    # ------------------------------------------------------------- fetching
    def fetch_ordered(self, heights, hashes):
        """
        Yield (index, result, latency) in order, where result is the analysed and
        verified block (dict) or the BlockInvalid it raised. Fetching runs in a thread
        pool and analysis in worker processes, both sized by the speed profile.
        """
        prof = PROFILES[self.config.speed_profile]
        workers = prof["workers"]
        if self.latency_ewma and self.latency_floor and self.latency_ewma > 3 * self.latency_floor \
                and self.latency_ewma > 0.5:
            workers = 1      # node seems busy: back off
        apool = self.analysis_pool(prof["procs"])

        def job(h, hx):
            t = time.monotonic()
            raw = self.node.block(hx)
            lat = time.monotonic() - t
            try:
                if apool is not None:
                    res = apool.submit(worker.analyze_in_worker, raw, h, hx).result()
                else:
                    res = worker.analyze_verified(raw, h, hx, self.dust_start(), self._policy_obj)
            except mr.BlockInvalid as e:
                res = e
            return res, lat

        if workers == 1:
            for i, (h, hx) in enumerate(zip(heights, hashes)):
                res, lat = job(h, hx)
                yield i, res, lat
            return

        ex = self.executor(workers)
        futs = collections.deque()
        try:
            it = iter(enumerate(zip(heights, hashes)))
            for _ in range(workers * 2):
                try:
                    i, (h, hx) = next(it)
                    futs.append((i, ex.submit(job, h, hx)))
                except StopIteration:
                    break
            while futs:
                i, f = futs.popleft()
                res, lat = f.result()
                try:
                    j, (h, hx) = next(it)
                    futs.append((j, ex.submit(job, h, hx)))
                except StopIteration:
                    pass
                yield i, res, lat
        finally:
            for _, f in futs:
                f.cancel()

    def analysis_pool(self, procs):
        """Worker processes for parsing (spawned, not forked: safe with threads and SQLite)."""
        n = min(procs, max(0, (os.cpu_count() or 1) - 1))
        if n < 2:
            self.shutdown_analysis_pool()
            return None
        key = (n, self.dust_start(), bool(self._policy_obj))
        if self._apool is None or self._apool_key != key:
            self.shutdown_analysis_pool()
            ctx = multiprocessing.get_context("spawn")
            self._apool = concurrent.futures.ProcessPoolExecutor(
                max_workers=n, mp_context=ctx, initializer=worker.init, initargs=(key[1], key[2]))
            self._apool_key = key
        return self._apool

    def shutdown_analysis_pool(self):
        if self._apool is not None:
            self._apool.shutdown(wait=True, cancel_futures=True)
            self._apool = None
            self._apool_key = None

    def accept(self, height, hexhash, res, expected_prev):
        """Turn a fetch_ordered result into a verified block; refetch on a data check failure."""
        for attempt in range(3):
            if not isinstance(res, Exception):
                if expected_prev is not None and res["header"][4:36] != expected_prev:
                    raise mr.BlockInvalid("block does not connect to the expected parent")
                return res
            if attempt == 2:
                raise res
            self.status.event("warning", f"Block {height:,} failed the data check ({res}); fetching again")
            try:
                res = worker.analyze_verified(self.node.block(hexhash), height, hexhash,
                                              self.dust_start(), self._policy_obj)
            except mr.BlockInvalid as e:
                res = e

    def executor(self, workers):
        """One long-lived pool per session, so per-thread keep-alive connections are reused."""
        if self._pool is None or self._pool_workers != workers:
            self.shutdown_pool()
            self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fetch")
            self._pool_workers = workers
        return self._pool

    def shutdown_pool(self):
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=True)
            self._pool = None
            self._pool_workers = None

    def throttle(self, latency):
        prof = PROFILES[self.config.speed_profile]
        a = 0.2
        self.latency_ewma = latency if self.latency_ewma is None else (1 - a) * self.latency_ewma + a * latency
        if self.latency_floor is None or self.latency_ewma < self.latency_floor:
            self.latency_floor = self.latency_ewma
        else:
            self.latency_floor *= 1.0005      # let the baseline drift up slowly
        delay = max(prof["min_sleep"], prof["pause"] * latency)
        if self.latency_ewma > 3 * self.latency_floor and self.latency_ewma > 0.5:
            delay = max(delay, self.latency_ewma)      # adaptive backoff: node is busy
        if delay > 0:
            self.wake.wait(delay)
            self.wake.clear()

    # ------------------------------------------------------------- passes
    def quick_pass(self, tip, q):
        start = max(0, tip - q + 1)
        todo = [h for h in range(start, tip + 1) if not self.store.has_block(h)]
        self.status.event("info", f"Quick pass: analysing the latest {q:,} blocks ({start:,}–{tip:,})")
        t0 = time.monotonic()
        done = q - len(todo)
        hashes_all = {}
        for i in range(0, len(todo), 500):
            chunk = todo[i:i + 500]
            for h, hx in zip(chunk, self.node.rpc.batch([("getblockhash", [h]) for h in chunk])):
                hashes_all[h] = hx
        hashes = [hashes_all[h] for h in todo]
        last_commit = time.monotonic()
        self.store.begin()
        try:
            for i, res, lat in self.fetch_ordered(todo, hashes):
                h = todo[i]
                res = self.accept(h, hashes[i], res, None)
                self.store.put_block(h, res, utxo_done=False)
                done += 1
                self.meter.add(res["original"])
                bps, Bps = self.meter.rates()
                remaining = q - done
                self.status.set(phase="quick", height=h, tip=tip, quick_done=done, quick_total=q,
                                progress=round(done / q * 100, 2), blocks_per_s=bps, bytes_per_s=Bps,
                                eta_seconds=int(remaining / bps) if bps > 0 else None,
                                detail=self.describe_block(h, res))
                if time.monotonic() - last_commit > COMMIT_SECONDS:
                    self.store.commit()
                    self.store.begin()
                    last_commit = time.monotonic()
                    self.check_config()
                    if self.paused or self.stop_event.is_set():
                        break
                self.throttle(lat)
            else:
                self.store.set_meta("quick_done", "1")
                self.store.set_meta("quick_top", tip)
                self.status.event("info", f"Quick pass finished in {time.monotonic() - t0:.0f}s")
            self.store.commit()
        except BaseException:
            self.store.rollback()
            raise

    def follow_new_blocks_quick(self, tip):
        """While history is still pending, show newly mined blocks (statistics only)."""
        if self.store.get_meta("quick_done") != "1":
            return
        top = int(self.store.get_meta("quick_top", tip))
        if tip <= top:
            return
        self.store.begin()
        try:
            for h in range(max(top + 1, tip - 9), tip + 1):
                if self.store.has_block(h):
                    continue
                hx = self.node.block_hash(h)
                try:
                    res = worker.analyze_verified(self.node.block(hx), h, hx, self.dust_start(), self._policy_obj)
                except mr.BlockInvalid as e:
                    res = e
                res = self.accept(h, hx, res, None)
                self.store.put_block(h, res, utxo_done=False)
                self.status.event("info", f"New block {h:,}: {self.describe_block(h, res)}")
            self.store.set_meta("quick_top", tip)
            self.store.commit()
        except BaseException:
            self.store.rollback()
            raise

    def skip_pruned(self, info, nxt):
        """If the node has pruned past our cursor, jump forward instead of retrying forever."""
        ph = int(info.get("pruneheight") or 0) if info.get("pruned") else 0
        if ph > nxt:
            self.store.begin()
            self.store.set_meta("full_next_height", ph)
            self.store.set_meta("full_start_height", ph)
            self.store.set_meta("utxo_complete", "0")
            self.store.commit()
            self.status.event("warning", f"Your node has pruned blocks {nxt:,}–{ph - 1:,} before they could be "
                                         f"scanned; continuing from {ph:,}. Spam UTXO figures are incomplete.")
            return True
        return False

    def full_pass(self, tip, info):
        nxt = int(self.store.get_meta("full_next_height"))
        if self.skip_pruned(info, nxt):
            return
        start_height = int(self.store.get_meta("full_start_height", 0))
        if self.processed_bytes == 0:
            self.processed_bytes = self.store.processed_bytes()
        batch = min(100, tip - nxt + 1)
        heights = list(range(nxt, nxt + batch))
        hashes = self.node.block_hashes(nxt, batch)
        expected_prev = self.store.block_hash_at(nxt - 1) if nxt > start_height else None
        last_commit = time.monotonic()
        blocks_since = 0
        last_applied = nxt - 1
        t_start = self.status.get("full_started_at") or time.time()
        self.status.set(full_started_at=t_start)
        self.store.begin()
        try:
            for i, res, lat in self.fetch_ordered(heights, hashes):
                h = heights[i]
                try:
                    res = self.accept(h, hashes[i], res, expected_prev)
                except mr.BlockInvalid as e:
                    if "parent" in str(e):
                        self.store.commit()
                        self.status.event("warning", f"Block {h:,} does not connect: chain changed while "
                                                     "scanning; checking for a reorganisation")
                        self.handle_reorg(int(self.node.chain_info()["blocks"]))
                        return
                    raise
                delta = self.store.apply_block_utxo(h, res, self.bloom)
                self.store.put_block(h, res, utxo_done=True, utxo_delta=delta)
                self.store.set_meta("full_next_height", h + 1)
                last_applied = h
                expected_prev = mr.block_hash(res["header"])
                self.processed_bytes += res["original"]
                blocks_since += 1
                self.meter.add(res["original"])
                self.update_full_status(h, tip, start_height, res)
                if time.monotonic() - last_commit > COMMIT_SECONDS or blocks_since >= COMMIT_BLOCKS:
                    self.store.prune_undo(h - REORG_WINDOW)
                    self.store.commit()
                    self.store.begin()
                    last_commit = time.monotonic()
                    blocks_since = 0
                    self.check_config()
                    if self.paused or self.stop_event.is_set():
                        break
                self.throttle(lat)
            self.store.prune_undo(last_applied - REORG_WINDOW)
            self.store.commit()
        except (mr.BlockInvalid, NodeError):
            # Failure happened before the current block was applied: everything in the
            # open transaction is complete block-by-block, so keep that progress.
            self.store.commit()
            info2 = self.node.chain_info()
            if self.skip_pruned(info2, int(self.store.get_meta("full_next_height"))):
                return
            raise
        except BaseException:
            self.store.rollback()
            raise
        if int(self.store.get_meta("full_next_height")) > tip:
            if self.store.get_meta("full_done") != "1":
                self.store.set_meta("full_done", "1")
                self.status.event("info", "Full history scan complete — now following new blocks live")

    def update_full_status(self, h, tip, start_height, res):
        bps, Bps = self.meter.rates()
        total = self.total_bytes_estimate
        if total and total > self.processed_bytes:
            progress = self.processed_bytes / total * 100
            eta = (total - self.processed_bytes) / Bps if Bps > 0 else None
        else:
            span = max(1, tip - start_height + 1)
            progress = (h - start_height + 1) / span * 100
            eta = (tip - h) / bps if bps > 0 else None
        self.status.set(phase="full", height=h, tip=tip, progress=round(min(progress, 99.99), 2),
                        blocks_per_s=bps, bytes_per_s=Bps, eta_seconds=int(eta) if eta else None,
                        processed_bytes=self.processed_bytes, total_bytes_estimate=total,
                        detail=self.describe_block(h, res))

    def describe_block(self, h, res):
        t = datetime.datetime.fromtimestamp(mr.header_time(res["header"]), datetime.timezone.utc)
        spam = sum(res[k] for k in mr.CARRIERS)
        pct = spam / res["original"] * 100 if res["original"] else 0
        return (f"Analysing block {h:,} ({t:%Y-%m-%d}) — {res['original'] / 1e6:.2f} MB, "
                f"{res['tx_count']:,} txs, {pct:.1f} % spam")

    # ------------------------------------------------------------- UTXO set size
    def maybe_utxo_info(self, force_first=False):
        last = float(self.store.get_meta("utxo_info_time", 0))
        if last and time.time() - last < UTXO_INFO_INTERVAL:
            return
        if force_first and last:
            return
        prev_phase = self.status.get("phase")
        self.status.set(phase="utxo_info", detail="Asking the node for the total UTXO set size "
                                                  "(read-only, may take a few minutes)")
        c = self.config
        try:
            proxy = None
            if c.tor_proxy:
                host, port = c.tor_proxy.rsplit(":", 1)
                proxy = (host.strip("[]"), int(port))
            slow = RPCClient(c.rpc_url, user=c.rpc_user or None, password=c.rpc_password,
                             cookie_file=c.rpc_cookie_file or None, timeout=1800, proxy=proxy,
                             cafile=c.rpc_cafile or None)
            res = slow.call("gettxoutsetinfo", "none")
            slow.close()
            import json
            self.store.set_meta("utxo_info", json.dumps(res))
            self.store.set_meta("utxo_info_time", time.time())
            self.status.event("info", f"UTXO set: {res.get('txouts', 0):,} entries")
        except NodeError as e:
            self.store.set_meta("utxo_info_time", time.time() - UTXO_INFO_INTERVAL + 3600)
            self.status.event("warning", f"Could not read the UTXO set size ({e}); will retry later")
        self.status.set(phase=prev_phase)
