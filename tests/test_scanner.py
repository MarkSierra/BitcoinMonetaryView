# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import shutil
import sqlite3
import tempfile
import time
import unittest

from bitcoinmonetaryview.analytics import Analytics
from bitcoinmonetaryview.config import Config
from bitcoinmonetaryview.rules import monetary_rules as mr
from bitcoinmonetaryview.scanner import Scanner, in_window
from bitcoinmonetaryview.store import BLOCK_COLS, Bloom, Store
from tests import blockgen as bg
from tests.mocknode import MockChain, MockNode


def spending_maker(chain):
    """Each block also spends the previous block's stamp multisig and dust outputs."""
    def maker(h, prev):
        extra = []
        if chain.blocks:
            res = mr.analyze_block(chain.blocks[-1][0], h - 1, dust_start_height=0)
            ins = [(txid, vout, b"") for txid, vout, *_ in res["utxo_spam"]]
            if ins:
                extra.append(bg.tx(ins, [(1000, bg.p2wpkh(b"sweep%d" % h))]))
        raw, bh = bg.mixed_block(h, prev=prev, seed=h)
        # rebuild: mixed block txs + sweep (rebuild block with extra tx)
        base = _mixed_txs(h)
        return bg.block(base + extra, h, prev)
    return maker


def _mixed_txs(seed):
    # Same content as bg.mixed_block but returned as tx list
    import tests.blockgen as g
    return [
        g.tx([(g.fake_prev(seed * 10 + 1), 0, b"")], [(100000, g.p2wpkh(b"a"))], witnesses=[[b"s" * 71, g.REAL_KEY]]),
        g.tx([(g.fake_prev(seed * 10 + 2), 0, b"")], [(546, g.p2tr(b"post%d" % seed))],
             witnesses=[g.inscription_witness(1200)]),
        g.tx([(g.fake_prev(seed * 10 + 3), 1, b"\x00" * 10)], [(0, g.op_return(120)), (5000, g.p2wpkh(b"c"))]),
        g.tx([(g.fake_prev(seed * 10 + 4), 0, b"\x00" * 10)], [(7800 + seed, g.stamp_multisig())]),
        g.tx([(g.fake_prev(seed * 10 + 5), 0, b"\x01" * 1700)], [(9000, g.p2wpkh(b"d"))]),
    ]


def brute_force_utxo(chain):
    utxo = {}
    for h, (raw, _) in enumerate(chain.blocks):
        res = mr.analyze_block(raw, h, dust_start_height=0)
        for txid, vout, value, kind, slen in res["utxo_spam"]:
            utxo[(txid, vout)] = (h, kind)
        for prev, vout in res["spends"]:
            utxo.pop((prev, vout), None)
    return utxo


class ScannerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.chain = MockChain()
        mk = spending_maker(self.chain)
        for _ in range(40):
            self.chain.append(mk)
        self.node = MockNode(self.chain)
        self.scanners = []

    def tearDown(self):
        for s in self.scanners:
            s.stop()
            s.join(10)
        self.node.stop()
        shutil.rmtree(self.dir)

    def config(self, **kw):
        cli = {"rpc_url": self.node.url, "rpc_user": "u", "rpc_password": "p", "speed_profile": "full",
               "quick_pass_blocks": "5", "bloom_mb": "8"}
        cli.update(kw)
        return Config(self.dir, cli=cli, env={})

    def start(self, **kw):
        s = Scanner(self.config(**kw))
        self.scanners.append(s)
        s.start()
        return s

    def wait_phase(self, s, phase, timeout=60, cond=None):
        t = time.time()
        while time.time() - t < timeout:
            if s.status.get("phase") == phase and (cond is None or cond()):
                return True
            time.sleep(0.05)
        self.fail(f"phase {phase} not reached; status={s.status.snapshot()}")

    def db_utxo(self):
        db = sqlite3.connect(os.path.join(self.dir, "regtest", "bmv.sqlite"))
        rows = db.execute("SELECT txid, vout, height, kind FROM spam_utxo").fetchall()
        hist = db.execute("SELECT COALESCE(SUM(count),0) FROM utxo_hist").fetchone()[0]
        db.close()
        return {(bytes(t), v): (h, k) for t, v, h, k in rows}, hist

    def tip_ok(self, s):
        return lambda: s.status.get("tip") == len(self.chain.blocks) - 1

    def test_full_scan_matches_brute_force(self):
        s = self.start()
        self.wait_phase(s, "live", cond=self.tip_ok(s))
        utxo, hist = self.db_utxo()
        expect = brute_force_utxo(self.chain)
        self.assertEqual(utxo, expect)
        self.assertEqual(hist, len(expect))
        self.assertGreater(len(expect), 0)
        a = Analytics(os.path.join(self.dir, "regtest", "bmv.sqlite"))
        summ = a.summary()
        self.assertEqual(summ["blocks_scanned"], 40)
        self.assertEqual(summ["utxo"]["count"], len(expect))
        self.assertTrue(summ["utxo"]["complete"])
        self.assertEqual(sum(b["count"] for b in summ["utxo"]["age_bands"]), len(expect))
        self.assertGreater(summ["spam_pct"], 10)
        # forbidden methods were never sent
        for m, *_ in self.node.calls:
            self.assertIn(m, ("REST", "getblockchaininfo", "getnetworkinfo", "getbestblockhash",
                              "getblockhash", "getblock", "gettxoutsetinfo", "getblockheader"))

    def test_new_blocks_and_reorg(self):
        s = self.start()
        self.wait_phase(s, "live", cond=self.tip_ok(s))
        mk = spending_maker(self.chain)
        for _ in range(3):
            self.chain.append(mk)
        self.wait_phase(s, "live", cond=self.tip_ok(s))
        # replace the last 4 blocks with a longer alternative branch
        with self.chain.lock:
            del self.chain.blocks[-4:]
        for _ in range(6):
            self.chain.append(lambda h, prev: bg.mixed_block(h, prev=prev, seed=5000 + h))
        self.wait_phase(s, "live", cond=self.tip_ok(s))
        time.sleep(0.5)
        utxo, hist = self.db_utxo()
        self.assertEqual(utxo, brute_force_utxo(self.chain))
        self.assertEqual(hist, len(utxo))
        msgs = " ".join(e["message"] for e in s.status.snapshot()["activity"])
        self.assertIn("reorganisation", msgs)

    def test_resume_after_stop(self):
        self.node.delay = 0.02
        s = self.start(speed_profile="eco", quick_pass_blocks="0")
        t = time.time()
        while s.status.get("height", 0) < 10 and time.time() - t < 30:
            time.sleep(0.02)
        s.stop()
        s.join(10)
        self.node.delay = 0
        s2 = self.start(quick_pass_blocks="0")
        self.wait_phase(s2, "live", cond=self.tip_ok(s2))
        utxo, hist = self.db_utxo()
        self.assertEqual(utxo, brute_force_utxo(self.chain))
        self.assertEqual(hist, len(utxo))

    # ---- saved spent-output filter
    def filter_file(self):
        return os.path.join(self.dir, "regtest", "spent-filter.bin")   # Store.bloom_path

    def meta(self, key):
        db = sqlite3.connect(os.path.join(self.dir, "regtest", "bmv.sqlite"))
        r = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        db.close()
        return None if r is None else r[0]

    def events(self, s):
        return [e["message"] for e in s.status.snapshot()["activity"]]

    def run_to_tip_and_stop(self, **kw):
        s = self.start(**kw)
        self.wait_phase(s, "live", cond=self.tip_ok(s))
        s.stop()
        s.join(10)
        return s

    def restart_and_spend_old_outputs(self, **kw):
        """Restart, then mine blocks that spend spam outputs created before the restart."""
        s = self.start(**kw)
        self.wait_phase(s, "live", cond=self.tip_ok(s))
        mk = spending_maker(self.chain)
        for _ in range(3):
            self.chain.append(mk)
        self.wait_phase(s, "live", cond=self.tip_ok(s))
        time.sleep(0.3)
        utxo, hist = self.db_utxo()
        self.assertEqual(utxo, brute_force_utxo(self.chain))
        self.assertEqual(hist, len(utxo))
        return s

    def test_saved_filter_used_after_clean_stop(self):
        self.run_to_tip_and_stop()
        self.assertTrue(os.path.exists(self.filter_file()))
        self.assertIsNotNone(self.meta("bloom_token"))
        s = self.restart_and_spend_old_outputs()
        self.assertTrue(any("from its saved copy" in m for m in self.events(s)))
        # used once: invalidated before the database changes again
        self.assertIsNone(self.meta("bloom_token"))
        self.assertFalse(os.path.exists(self.filter_file()))

    def test_saved_filter_not_used_after_crash(self):
        self.run_to_tip_and_stop()
        s = self.start()
        s.save_bloom = lambda: None            # a crash: no save at the end
        self.wait_phase(s, "live", cond=self.tip_ok(s))
        self.chain.append(spending_maker(self.chain))
        self.wait_phase(s, "live", cond=self.tip_ok(s))
        s.stop()
        s.join(10)
        s2 = self.restart_and_spend_old_outputs()
        msgs = self.events(s2)
        self.assertFalse(any("from its saved copy" in m for m in msgs))
        self.assertTrue(any(m.startswith("Spent-output filter loaded (") for m in msgs))

    def test_damaged_or_resized_saved_filter_is_rebuilt(self):
        for change in ("damage", "resize"):
            with self.subTest(change):
                self.run_to_tip_and_stop()
                kw = {}
                if change == "damage":
                    with open(self.filter_file(), "r+b") as f:
                        f.seek(-1, os.SEEK_END)
                        b = f.read(1)
                        f.seek(-1, os.SEEK_END)
                        f.write(bytes([b[0] ^ 0xFF]))
                else:
                    kw["bloom_mb"] = "16"
                s = self.restart_and_spend_old_outputs(**kw)
                self.assertFalse(any("from its saved copy" in m for m in self.events(s)))
                s.stop()
                s.join(10)

    def test_rescan_discards_saved_filter(self):
        self.run_to_tip_and_stop()
        s = self.start()
        self.wait_phase(s, "live", cond=self.tip_ok(s))
        s.request_rescan()
        self.wait_phase(s, "live", cond=lambda: self.meta("full_done") == "1"
                        and s.status.get("tip") == len(self.chain.blocks) - 1)
        utxo, _ = self.db_utxo()
        self.assertEqual(utxo, brute_force_utxo(self.chain))

    def test_tampered_block_stops_scan(self):
        self.node.tamper_heights = {7}
        s = self.start(quick_pass_blocks="0")
        self.wait_phase(s, "error", timeout=30)
        db = sqlite3.connect(os.path.join(self.dir, "regtest", "bmv.sqlite"))
        nxt = int(db.execute("SELECT value FROM meta WHERE key='full_next_height'").fetchone()[0])
        db.close()
        self.assertEqual(nxt, 7)

    def test_waits_during_ibd(self):
        self.node.ibd = True
        s = self.start()
        self.wait_phase(s, "waiting_node", timeout=20)
        self.assertIn("syncing", s.status.get("detail"))

    def test_pause(self):
        self.node.delay = 0.05
        s = self.start(quick_pass_blocks="0", speed_profile="eco")
        time.sleep(0.5)
        s.pause()
        self.wait_phase(s, "paused", timeout=20)
        h = s.status.get("height")
        time.sleep(0.5)
        self.assertEqual(s.status.get("height"), h)
        s.resume()
        self.node.delay = 0
        self.wait_phase(s, "live", cond=self.tip_ok(s))

    def test_settings_file_change_applies_live(self):
        s = self.start()
        self.wait_phase(s, "live", cond=self.tip_ok(s))
        s.config.update_file({"speed_profile": "balanced"}) if s.config.can_edit("speed_profile") else None
        # speed_profile was given on the CLI here, so it is pinned and not editable:
        self.assertFalse(s.config.can_edit("speed_profile"))
        self.assertTrue(s.config.can_edit("scan_window"))
        s.config.update_file({"scan_window": "01:00-02:00"})
        self.assertEqual(s.config.scan_window, "01:00-02:00")


    def test_sample_pass_runs_before_full_scan(self):
        from bitcoinmonetaryview import scanner as sc
        old = sc.MIN_SAMPLE_EVERY, sc.MIN_SAMPLE_BLOCKS
        sc.MIN_SAMPLE_EVERY, sc.MIN_SAMPLE_BLOCKS = 1, 2
        try:
            s = self.start(sample_every="3")
            self.wait_phase(s, "live", cond=self.tip_ok(s))
        finally:
            sc.MIN_SAMPLE_EVERY, sc.MIN_SAMPLE_BLOCKS = old
        db = sqlite3.connect(os.path.join(self.dir, "regtest", "bmv.sqlite"))
        heights = [r[0] for r in db.execute("SELECT height FROM sample ORDER BY height")]
        meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
        db.close()
        # every 3rd block below the quick pass (blocks 35-39)
        self.assertEqual(heights, list(range(0, 35, 3)))
        self.assertEqual(meta["sample_done"], "1")
        self.assertEqual(meta["sample_top"], "35")
        self.assertIn("Sample pass", " ".join(e["message"] for e in s.status.snapshot()["activity"]))
        # exact results are unaffected, and no estimate once the full scan is done
        summ = Analytics(os.path.join(self.dir, "regtest", "bmv.sqlite")).summary()
        self.assertEqual(summ["blocks_scanned"], 40)
        self.assertIsNone(summ["estimate"])
        self.assertEqual(self.db_utxo()[0], brute_force_utxo(self.chain))


    def test_new_blocks_shown_during_history_scan(self):
        from bitcoinmonetaryview import scanner as sc
        old = sc.FOLLOW_TIP_SECONDS, sc.FULL_BATCH
        sc.FOLLOW_TIP_SECONDS, sc.FULL_BATCH = 0, 5
        try:
            self.node.delay = 0.1
            s = self.start(speed_profile="eco")
            self.wait_phase(s, "full", timeout=60)
            mk = spending_maker(self.chain)
            for _ in range(2):
                self.chain.append(mk)
            new_tip = len(self.chain.blocks) - 1
            path = os.path.join(self.dir, "regtest", "bmv.sqlite")
            t = time.time()
            while time.time() - t < 30:
                db = sqlite3.connect(path)
                top, done = db.execute("SELECT MAX(height), (SELECT value FROM meta WHERE key='full_done') "
                                       "FROM blocks").fetchone()
                db.close()
                if top == new_tip:
                    break
                time.sleep(0.1)
            self.assertEqual(top, new_tip)
            self.assertIsNone(done)          # picked up while the history scan was still running
            self.node.delay = 0
            self.wait_phase(s, "live", cond=self.tip_ok(s))
            self.assertEqual(self.db_utxo()[0], brute_force_utxo(self.chain))
        finally:
            sc.FOLLOW_TIP_SECONDS, sc.FULL_BATCH = old


    def test_new_block_fetched_once_after_full_scan(self):
        from bitcoinmonetaryview import scanner as sc
        old = sc.FOLLOW_TIP_SECONDS
        sc.FOLLOW_TIP_SECONDS = 0
        try:
            s = self.start()
            self.wait_phase(s, "live", cond=self.tip_ok(s))
            n_calls = len(self.node.calls)
            self.chain.append(spending_maker(self.chain))
            self.wait_phase(s, "live", cond=self.tip_ok(s))
            time.sleep(0.3)
            new_hex = self.chain.tip_hex()
            fetches = [c for c in self.node.calls[n_calls:]
                       if new_hex in str(c) and (c[0] == "getblock" or "/rest/block/" in str(c))]
            self.assertEqual(len(fetches), 1, fetches)
            self.assertNotIn("New block", " ".join(e["message"] for e in s.status.snapshot()["activity"]))
        finally:
            sc.FOLLOW_TIP_SECONDS = old

    def test_new_block_during_scan_is_not_a_reorg(self):
        from bitcoinmonetaryview import scanner as sc
        old = sc.FOLLOW_TIP_SECONDS, sc.FULL_BATCH
        sc.FOLLOW_TIP_SECONDS, sc.FULL_BATCH = 0, 2
        try:
            self.node.delay = 0.05
            s = self.start(speed_profile="eco", sample_every="0")
            self.wait_phase(s, "full", timeout=60)
            self.chain.append(spending_maker(self.chain))
            self.node.delay = 0
            self.wait_phase(s, "live", cond=self.tip_ok(s))
            msgs = " ".join(e["message"] for e in s.status.snapshot()["activity"])
            self.assertNotIn("reorganisation", msgs)
            self.assertEqual(self.db_utxo()[0], brute_force_utxo(self.chain))
        finally:
            sc.FOLLOW_TIP_SECONDS, sc.FULL_BATCH = old

    def test_catch_up_after_downtime_leaves_no_hole(self):
        from bitcoinmonetaryview import scanner as sc
        old = sc.FOLLOW_TIP_SECONDS
        sc.FOLLOW_TIP_SECONDS = 0
        path = os.path.join(self.dir, "regtest", "bmv.sqlite")
        try:
            self.node.delay = 0.05
            s = self.start(speed_profile="eco", sample_every="0", quick_pass_blocks="5")
            self.wait_phase(s, "full", timeout=60)
            s.stop()
            s.join(10)
            mk = spending_maker(self.chain)
            for _ in range(15):                      # more new blocks than the old 10-block window
                self.chain.append(mk)
            tip = len(self.chain.blocks) - 1
            s2 = self.start(speed_profile="eco", sample_every="0", quick_pass_blocks="5")
            t = time.time()
            while time.time() - t < 30:
                db = sqlite3.connect(path)
                qt = db.execute("SELECT value FROM meta WHERE key='quick_top'").fetchone()
                db.close()
                if qt and int(qt[0]) == tip:
                    break
                time.sleep(0.05)
            db = sqlite3.connect(path)
            have = {r[0] for r in db.execute("SELECT height FROM blocks WHERE height>=35")}
            db.close()
            self.assertEqual(have, set(range(35, tip + 1)))       # contiguous up to the tip
            self.node.delay = 0
            self.wait_phase(s2, "live", cond=self.tip_ok(s2))
            self.assertEqual(self.db_utxo()[0], brute_force_utxo(self.chain))
        finally:
            sc.FOLLOW_TIP_SECONDS = old

    def test_sample_grid_kept_when_setting_changes(self):
        from bitcoinmonetaryview import scanner as sc
        s = Scanner(self.config(sample_every="250"))
        s.store = Store(os.path.join(self.dir, "plan.sqlite"))
        s.store.set_meta("sample_top", 5000)
        s.store.set_meta("sample_every", 100)
        heights, end, n = s.sample_plan(5000, 0)
        self.assertEqual((n, end), (100, 5000))
        self.assertEqual(heights, list(range(0, 5000, 100)))
        s.store.close()

    def test_eta_in_tiny_early_blocks_uses_sample_rate(self):
        # 2009-style blocks: 100 blocks/s but only 25 kB/s. Bytes left / 25 kB/s said "400 days";
        # the sample pass measured 10 MB/s over the whole chain.
        s = Scanner(self.config())
        s.store = Store(os.path.join(self.dir, "eta.sqlite"))
        s.total_bytes_estimate, tip = 780e9, 974_000
        s.meter.rates = lambda: (100.0, 25e3)
        s.describe_block = lambda h, res: ""
        s.update_full_status(8000, tip, 0, {"original": 250})
        self.assertGreater(s.status.get("eta_seconds"), 300 * 86400)    # no sample rate yet: as before
        s.store.set_meta("sample_bytes_per_s", 10e6)
        s._sample_rate = None                                           # as the sample pass does
        s.update_full_status(8001, tip, 0, {"original": 250})
        self.assertAlmostEqual(s.status.get("eta_seconds"), 78_000, delta=1000)
        s.meter.rates = lambda: (8.0, 8e6)                              # full blocks: measured rate again
        s.update_full_status(900_000, tip, 0, {"original": 1_000_000})
        self.assertAlmostEqual(s.status.get("eta_seconds"), 97_500, delta=1000)
        s.store.close()


class PrunedNode(ScannerTest):
    def test_jumps_when_node_prunes_past_cursor(self):
        self.node.pruned = True
        self.node.prune_height = 0
        self.node.delay = 0.05
        s = self.start(quick_pass_blocks="0", speed_profile="eco")
        t = time.time()
        while (s.status.get("height") or 0) < 3 and time.time() - t < 30:
            time.sleep(0.02)
        s.pause()
        self.wait_phase(s, "paused", timeout=20)
        self.node.prune_height = 25          # node prunes past our cursor while we are paused
        self.node.delay = 0
        s.resume()
        self.wait_phase(s, "live", cond=self.tip_ok(s))
        db = sqlite3.connect(os.path.join(self.dir, "regtest", "bmv.sqlite"))
        meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
        db.close()
        self.assertEqual(meta["utxo_complete"], "0")
        self.assertIn("pruned blocks", " ".join(e["message"] for e in s.status.snapshot()["activity"]))

    def test_sample_pass_skips_pruned_blocks(self):
        from bitcoinmonetaryview import scanner as sc
        old = sc.MIN_SAMPLE_EVERY, sc.MIN_SAMPLE_BLOCKS
        sc.MIN_SAMPLE_EVERY, sc.MIN_SAMPLE_BLOCKS = 1, 2
        try:
            self.node.pruned = True
            self.node.prune_height = 0
            self.node.delay = 0.1
            s = self.start(speed_profile="eco", sample_every="3")
            self.wait_phase(s, "sample", timeout=60)
            self.node.prune_height = 30          # the node prunes the blocks the sample pass is after
            self.node.delay = 0
            self.wait_phase(s, "live", cond=self.tip_ok(s))
        finally:
            sc.MIN_SAMPLE_EVERY, sc.MIN_SAMPLE_BLOCKS = old
        db = sqlite3.connect(os.path.join(self.dir, "regtest", "bmv.sqlite"))
        meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
        db.close()
        self.assertEqual(meta["sample_done"], "1")
        self.assertEqual(meta["full_start_height"], "30")


class DamagedSettings(unittest.TestCase):
    """Non-default spam rules in settings.json, and the file becomes unreadable or gets a bad value:
    the results must not be deleted because the rules silently fell back to their defaults."""
    setUp, tearDown = ScannerTest.setUp, ScannerTest.tearDown
    config, start, wait_phase, tip_ok = ScannerTest.config, ScannerTest.start, ScannerTest.wait_phase, ScannerTest.tip_ok

    def write(self, text):
        with open(os.path.join(self.dir, "settings.json"), "w") as f:
            f.write(text)

    def results(self):
        db = sqlite3.connect(os.path.join(self.dir, "regtest", "bmv.sqlite"))
        n = db.execute("SELECT COUNT(*) FROM blocks WHERE utxo_done=1").fetchone()[0]
        rid = db.execute("SELECT value FROM meta WHERE key='rules_id'").fetchone()[0]
        db.close()
        return n, rid

    def block_fetches(self):
        return sum(1 for m, p in list(self.node.calls) if m == "getblock" or (m == "REST" and "/rest/block/" in p))

    def wait_warning(self, s, text, timeout=30):
        t = time.time()
        while time.time() - t < timeout:
            if any(text in w for w in s.config.warnings):
                return
            time.sleep(0.05)
        self.fail(f"no warning with {text!r}: {s.config.warnings}")

    def assert_results_kept(self, s):
        n, rid = self.results()
        self.assertEqual(n, 40)
        self.assertIn("policy:off", rid)
        self.assertNotIn("Spam rules changed", " ".join(e["message"] for e in s.status.snapshot()["activity"]))

    def scanned_with_policy_off(self):
        self.write('{"carrier_policy": false}')
        s = self.start()
        self.wait_phase(s, "live", cond=self.tip_ok(s))
        self.assertEqual(self.results()[0], 40)
        return s

    def test_file_damaged_while_running_keeps_rules_and_results(self):
        s = self.scanned_with_policy_off()
        self.write('{"carrier_policy": false,')                 # cut short
        self.wait_warning(s, "could not be read")
        time.sleep(1.5)                                         # time for a (wrong) restart and reset
        self.assertFalse(s.config.carrier_policy)
        self.assert_results_kept(s)
        self.write('{"carrier_policy": "flase"}')               # one bad value
        self.wait_warning(s, "invalid value")
        time.sleep(1.5)
        self.assertFalse(s.config.carrier_policy)
        self.assert_results_kept(s)
        self.assertEqual(s.status.get("phase"), "live")

    def test_file_damaged_at_start_holds_the_scan_and_keeps_results(self):
        s = self.scanned_with_policy_off()
        s.stop()
        s.join(20)
        self.write('{"carrier_policy": false,')
        fetched = self.block_fetches()
        s2 = self.start()
        self.wait_phase(s2, "error", timeout=20)
        self.assertIn("settings.json", s2.status.get("detail"))
        self.assert_results_kept(s2)
        self.write('{"carrier_policy": false}')                 # repaired: the scan goes on, nothing rescanned
        s2.wake.set()
        self.wait_phase(s2, "live", cond=self.tip_ok(s2))
        self.assert_results_kept(s2)
        self.assertEqual(self.block_fetches(), fetched)

    def test_bad_rules_value_at_start_holds_the_scan(self):
        s = self.scanned_with_policy_off()
        s.stop()
        s.join(20)
        self.write('{"carrier_policy": "flase"}')
        s2 = self.start()
        self.wait_phase(s2, "error", timeout=20)
        self.assert_results_kept(s2)

    def test_damaged_file_at_start_with_default_rules_scans_on(self):
        s = self.start()                                        # no settings file: default rules
        self.wait_phase(s, "live", cond=self.tip_ok(s))
        s.stop()
        s.join(20)
        self.write("{not json")
        self.chain.append(spending_maker(self.chain))
        s2 = self.start()
        self.wait_phase(s2, "live", cond=self.tip_ok(s2))
        db = sqlite3.connect(os.path.join(self.dir, "regtest", "bmv.sqlite"))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM blocks WHERE utxo_done=1").fetchone()[0], 41)
        db.close()


class InterruptedQuickPass(unittest.TestCase):
    """A quick pass is stopped, the chain grows by more blocks than the pass had done, and the pass runs
    again: the exact results at the top must stay one range, as coverage and estimate assume."""
    setUp, tearDown, config = ScannerTest.setUp, ScannerTest.tearDown, ScannerTest.config

    def stored(self):
        db = sqlite3.connect(os.path.join(self.dir, "regtest", "bmv.sqlite"))
        heights = [h for (h,) in db.execute("SELECT height FROM blocks ORDER BY height")]
        done = db.execute("SELECT value FROM meta WHERE key='quick_done'").fetchone()
        db.close()
        return heights, done

    def test_second_pass_after_chain_growth_leaves_no_hole(self):
        from bitcoinmonetaryview import scanner as sc

        class Prepared(Scanner):
            def loop(self):             # connect and open the database as every session does, then return
                pass

        s = Prepared(self.config(speed_profile="eco", quick_pass_blocks="20", sample_every="0"))
        s.session()
        old, sc.COMMIT_SECONDS = sc.COMMIT_SECONDS, 0
        try:
            s.paused = True             # the pass ends at its first commit, as on a pause or a stop
            s.quick_pass(39, 20)
            self.assertEqual(self.stored(), ([20], None))
            mk = spending_maker(self.chain)
            for _ in range(30):         # the app was off for a while
                self.chain.append(mk)
            s.paused = False
            s.quick_pass(69, 20)
        finally:
            sc.COMMIT_SECONDS = old
            s.node.close()
            s.store.close()
        self.assertEqual(self.stored(), (list(range(50, 70)), ("1",)))
        cov = Analytics(os.path.join(self.dir, "regtest", "bmv.sqlite")).summary()["coverage"]
        self.assertEqual(cov["ranges"], [[50, 69]])
        self.assertEqual((cov["gap"]["from"], cov["gap"]["to"]), (0, 49))


class NewerDatabase(unittest.TestCase):
    """A database written by a newer schema is refused and left as it is (L8)."""

    def make(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        path = os.path.join(d, "regtest", "bmv.sqlite")
        s = Store(path)
        s.db.execute("INSERT INTO blocks(height, hash, utxo_done) VALUES(7, x'00', 1)")
        s.set_meta("schema_version", 99)
        s.close()
        return d, path

    def test_refused_and_untouched(self):
        from bitcoinmonetaryview import store
        d, path = self.make()
        before = open(path, "rb").read()
        with self.assertRaises(store.SchemaTooNew) as cm:
            Store(path)
        self.assertIn("newer version", str(cm.exception))
        self.assertEqual(open(path, "rb").read(), before)
        db = sqlite3.connect(path)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM blocks").fetchone()[0], 1)
        db.close()

    def test_scanner_reports_it(self):
        d, path = self.make()
        chain = MockChain()
        chain.build(3)
        node = MockNode(chain)
        self.addCleanup(node.stop)
        s = Scanner(Config(d, cli={"rpc_url": node.url, "rpc_user": "u", "rpc_password": "p"}, env={}))
        s.start()
        t = time.time()
        while s.status.get("phase") != "error" and time.time() - t < 20:
            time.sleep(0.05)
        s.stop()
        s.join(10)
        self.assertEqual(s.status.get("phase"), "error")
        self.assertIn("newer version", s.status.get("detail"))
        db = sqlite3.connect(path)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM blocks").fetchone()[0], 1)
        db.close()


class StatusTexts(ScannerTest):
    def test_pause_without_quick_pass_does_not_promise_new_blocks(self):
        """N3: with no quick pass, new blocks are not followed while paused, so the text must not say so."""
        self.node.delay = 0.05
        s = self.start(quick_pass_blocks="0", speed_profile="eco", sample_every="0")
        time.sleep(0.3)
        s.pause()
        self.wait_phase(s, "paused", timeout=20)
        self.assertNotIn("New blocks", s.status.get("detail"))
        self.assertIn("paused", s.status.get("detail"))

    def test_processed_bytes_follow_the_database_after_a_rollback(self):
        """N4: blocks rolled back must not stay counted in the progress figure."""
        calls = {"n": 0}
        orig = Scanner.throttle

        def failing(scanner, latency):
            calls["n"] += 1
            if calls["n"] == 5:
                raise RuntimeError("boom")
            return orig(scanner, latency)

        Scanner.throttle = failing
        self.addCleanup(setattr, Scanner, "throttle", orig)
        s = self.start(quick_pass_blocks="0", speed_profile="eco", sample_every="0")
        self.wait_phase(s, "error", timeout=30)
        s.stop()
        s.join(10)
        db = sqlite3.connect(os.path.join(self.dir, "regtest", "bmv.sqlite"))
        stored = db.execute("SELECT COALESCE(SUM(size),0) FROM blocks WHERE utxo_done=1").fetchone()[0]
        db.close()
        self.assertIn(s.processed_bytes, (0, stored))     # 0: re-read from the database on the next pass


class StoreRegressions(unittest.TestCase):
    def test_reset_clears_completion_flags(self):
        from bitcoinmonetaryview.store import Store
        d = tempfile.mkdtemp()
        try:
            st = Store(os.path.join(d, "x.sqlite"))
            for k in ("full_done", "quick_top", "utxo_complete", "full_next_height"):
                st.set_meta(k, "1")
            st.reset()
            for k in ("full_done", "quick_top", "utxo_complete", "full_next_height"):
                self.assertIsNone(st.get_meta(k), k)
            st.close()
        finally:
            shutil.rmtree(d)

    def _estimate_store(self, every):
        """Exact results for blocks 0-9 (full scan) and 30-39 (quick pass); sample of 10-29."""
        from bitcoinmonetaryview.store import Store
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        path = os.path.join(d, "x.sqlite")
        st = Store(path)
        truth = {"size": 0, "stored": 0, "spam": 0}
        st.begin()
        for h in range(40):
            raw, _ = bg.mixed_block(h, seed=h)
            if h % 4 == 0:      # vary the blocks so the estimate is not trivially exact
                raw, _ = bg.block([bg.tx([(bg.fake_prev(h), 0, b"")], [(5000, bg.p2wpkh(b"x%d" % h))])], h)
            res = mr.analyze_block(raw, h, dust_start_height=0)
            truth["size"] += res["original"]
            truth["stored"] += res["stored"]
            truth["spam"] += sum(res[k] for k in mr.CARRIERS)
            if h < 10 or h >= 30:
                st.put_block(h, res, utxo_done=h < 10)
            elif h % every == 0:
                st.put_sample(h, res)
        for k, v in (("full_next_height", 10), ("full_start_height", 0), ("sample_done", "1"),
                     ("sample_top", 30), ("sample_every", every)):
            st.set_meta(k, v)
        st.commit()
        return st, path, truth

    def test_estimate_is_exact_with_every_block_sampled(self):
        st, path, truth = self._estimate_store(every=1)
        est = Analytics(path).summary()["estimate"]
        self.assertEqual(est["blocks"], 40)
        self.assertEqual(est["samples"], 20)
        self.assertAlmostEqual(est["totals"]["size"], truth["size"])
        self.assertAlmostEqual(est["totals"]["stored"], truth["stored"])
        self.assertAlmostEqual(est["spam_bytes"], truth["spam"])
        self.assertAlmostEqual(est["spam_margin_pct"], 0, places=6)     # whole gap sampled
        st.close()

    def test_estimate_from_sample_and_reset(self):
        st, path, truth = self._estimate_store(every=2)
        summ = Analytics(path).summary()
        est = summ["estimate"]
        self.assertEqual(est["samples"], 10)
        self.assertLess(abs(est["spam_bytes"] - truth["spam"]) / truth["spam"], 0.25)
        self.assertGreater(est["spam_bytes"], summ["spam_bytes"])        # covers the unscanned gap
        self.assertGreater(est["spam_margin_pct"], 0)
        self.assertLess(est["exact_share_pct"], 100)
        st.reset()
        self.assertEqual(st.db.execute("SELECT COUNT(*) FROM sample").fetchone()[0], 0)
        for k in ("sample_done", "sample_top", "sample_every"):
            self.assertIsNone(st.get_meta(k))
        st.close()

    def test_coverage_and_period(self):
        st, path, truth = self._estimate_store(every=2)
        a = Analytics(path)
        cov = a.summary()["coverage"]
        self.assertFalse(cov["complete"])
        self.assertEqual(cov["ranges"], [[0, 9], [30, 39]])
        self.assertEqual((cov["gap"]["from"], cov["gap"]["to"]), (10, 29))
        p = a.period(lo=0, hi=39)
        self.assertEqual((p["blocks_scanned"], p["blocks_total"]), (20, 40))
        self.assertAlmostEqual(p["coverage_pct"], 50)
        p = a.period(lo=30, hi=39)
        self.assertAlmostEqual(p["coverage_pct"], 100)
        self.assertEqual(p["first"], 30)
        # all blocks share one timestamp here, so a time range covering it returns every scanned block
        p = a.period(start=1600000000, end=1800000000)
        self.assertEqual(p["blocks_scanned"], 20)
        self.assertTrue(a.period(start=1, end=2)["empty"])
        b = a.block(35)
        self.assertEqual(a.block_by_hash(b["hash"])["height"], 35)
        self.assertEqual(len(a.history()["months"]), 1)
        self.assertEqual(a.history()["coverage"]["gap"]["from"], 10)
        st.close()

    def test_period_before_scanned_blocks_is_not_fully_covered(self):
        st, path, truth = self._estimate_store(every=2)
        st.begin()
        st.db.execute("DELETE FROM blocks WHERE height<30")          # only the quick pass (30-39)
        st.db.execute("UPDATE blocks SET time=1700000000+height*600")
        st.commit()
        a = Analytics(path)
        p = a.period(start=1700000000 + 10 * 600, end=1700000000 + 39 * 600)
        self.assertEqual(p["blocks_scanned"], 10)
        self.assertEqual(p["blocks_total"], 30)                      # 10-39, extrapolated back
        self.assertAlmostEqual(p["coverage_pct"], 100 / 3)
        self.assertEqual(a.period(start=1600000000, end=1700000000)["blocks_total"], 1)    # clamped at 0
        plan = st.db.execute("EXPLAIN QUERY PLAN SELECT * FROM blocks WHERE hash=?", (b"x" * 32,)).fetchall()
        self.assertIn("blocks_hash", str(plan))
        st.close()

    def test_disconnect_restores_bloom_entries(self):
        from bitcoinmonetaryview.store import Store
        d = tempfile.mkdtemp()
        try:
            st = Store(os.path.join(d, "x.sqlite"))
            raw1, _ = bg.mixed_block(1)
            r1 = mr.analyze_block(raw1, 1, dust_start_height=0)
            spam = r1["utxo_spam"]
            r2 = dict(r1)
            r2["utxo_spam"] = []
            r2["spends"] = [(t, v) for t, v, *_ in spam]
            bloom = Bloom(1)
            st.begin()
            st.apply_block_utxo(1, r1, bloom)
            st.put_block(1, r1, True)
            st.apply_block_utxo(2, r2, bloom)
            st.put_block(2, r2, True)
            st.commit()
            self.assertEqual(st.spam_utxo_count(), 0)
            fresh = Bloom(1)                          # as if rebuilt after a restart
            st.begin()
            st.disconnect_block(2, fresh)
            st.commit()
            self.assertEqual(st.spam_utxo_count(), len(spam))
            self.assertTrue(all(fresh.maybe(t, v) for t, v, *_ in spam))
            st.close()
        finally:
            shutil.rmtree(d)


class Misc(unittest.TestCase):
    def test_window(self):
        import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("UTC")

        def at(hh, mm):
            return datetime.datetime(2026, 1, 1, hh, mm, tzinfo=tz)
        self.assertTrue(in_window("01:00-07:00", "UTC", at(3, 0)))
        self.assertFalse(in_window("01:00-07:00", "UTC", at(8, 0)))
        self.assertTrue(in_window("22:00-06:00", "UTC", at(23, 30)))
        self.assertTrue(in_window("22:00-06:00", "UTC", at(5, 59)))
        self.assertFalse(in_window("22:00-06:00", "UTC", at(6, 0)))
        self.assertTrue(in_window("", "UTC", at(12, 0)))

    def test_bloom_no_false_negatives(self):
        import os as _os
        b = Bloom(1)
        items = [(_os.urandom(32), i % 7) for i in range(20000)]
        for t, v in items:
            b.add(t, v)
        self.assertTrue(all(b.maybe(t, v) for t, v in items))
        fp = sum(b.maybe(_os.urandom(32), 0) for _ in range(20000))
        self.assertLess(fp, 200)

    def test_bloom_add_many_matches_add(self):
        items = [(os.urandom(32), i % 9) for i in range(20000)]
        for mb in (1, 64):
            a, b = Bloom(mb), Bloom(mb)
            for t, v in items:
                a.add(t, v)
            b.add_many(items)
            self.assertEqual((a.arr, a.count), (b.arr, b.count))
            self.assertTrue(all(b.maybe(t, v) for t, v in items))

    def test_bloom_false_positive_rate_at_mainnet_load(self):
        # ~48 million spam outputs in the default 256 MB filter is ~11.4 entries per
        # 512-bit block; the same load in a 1 MB filter (16,384 blocks).
        b = Bloom(1)
        b.add_many((os.urandom(32), 0) for _ in range(187_000))
        fp = sum(b.maybe(os.urandom(32), 0) for _ in range(200_000))
        self.assertLess(fp / 200_000, 1e-4)      # each one only costs a SQLite lookup

    def test_bloom_save_and_load(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "f.bin")
            b = Bloom(1)
            items = [(os.urandom(32), i % 5) for i in range(5000)]
            for t, v in items:
                b.add(t, v)
            tok = os.urandom(16)
            b.save(path, tok)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            c = Bloom.load(path, 1, tok)
            self.assertEqual((c.arr, c.count, c.bits), (b.arr, b.count, b.bits))
            self.assertTrue(all(c.maybe(t, v) for t, v in items))
            self.assertIsNone(Bloom.load(path, 1, os.urandom(16)))      # other token
            self.assertIsNone(Bloom.load(path, 2, tok))                 # other size
            self.assertIsNone(Bloom.load(os.path.join(d, "none"), 1, tok))
            with open(path, "r+b") as f:
                f.truncate(os.path.getsize(path) - 1)
            self.assertIsNone(Bloom.load(path, 1, tok))                 # truncated
            self.assertFalse(os.path.exists(path + ".tmp"))
        finally:
            shutil.rmtree(d)

    def test_config_self_healing(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "settings.json"), "w") as f:
                f.write('{"speed_profile": "warp", "port": "abc", "unknown": 1, "scan_window": "01:00-07:00",'
                        ' "timezone": "Mars/Base"}')
            c = Config(d, env={})
            self.assertEqual(c.speed_profile, "eco")
            self.assertEqual(c.port, 8338)
            self.assertEqual(c.scan_window, "01:00-07:00")
            self.assertEqual(c.timezone, "UTC")
            self.assertGreaterEqual(len(c.warnings), 3)
            with open(os.path.join(d, "settings.json"), "w") as f:
                f.write("not json")
            c.load()
            self.assertEqual(c.speed_profile, "eco")
        finally:
            shutil.rmtree(d)

    def settings_dir(self, text):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        with open(os.path.join(d, "settings.json"), "w") as f:
            f.write(text)
        return d

    def rewrite(self, d, text):
        with open(os.path.join(d, "settings.json"), "w") as f:
            f.write(text)

    def test_unreadable_file_keeps_the_previous_settings(self):
        d = self.settings_dir('{"auth_user": "bob", "auth_password": "pw", "carrier_policy": false, "bloom_mb": 64}')
        c = Config(d, env={})
        self.assertEqual(c.unknown, set())
        for damaged in ('{"auth_user": "bob",', "", "[1, 2]"):
            self.rewrite(d, damaged)
            c.load()
            self.assertEqual((c.auth_user, c.auth_password, c.carrier_policy, c.bloom_mb), ("bob", "pw", False, 64))
            self.assertEqual(c.sources["auth_user"], "file")
            self.assertEqual(c.unknown, set())
            self.assertIn("keeping the previous settings", " ".join(c.warnings))
        self.rewrite(d, '{"bloom_mb": 32}')                      # a valid file is applied as written
        c.load()
        self.assertEqual((c.auth_user, c.carrier_policy, c.bloom_mb), ("", True, 32))
        self.assertEqual(c.warnings, [])

    def test_invalid_value_keeps_the_previous_value(self):
        d = self.settings_dir('{"carrier_policy": false, "speed_profile": "full"}')
        c = Config(d, env={})
        self.rewrite(d, '{"carrier_policy": "flase", "speed_profile": "balanced"}')
        c.load()
        self.assertFalse(c.carrier_policy)
        self.assertEqual(c.speed_profile, "balanced")
        self.assertEqual(c.unknown, set())
        self.assertIn("keeping the previous value", " ".join(c.warnings))

    def test_unreadable_file_at_start_marks_its_settings_unknown(self):
        d = self.settings_dir('{"auth_user": "bob",')
        c = Config(d, env={})
        self.assertTrue(c.auth_unknown)
        self.assertTrue({"auth_user", "auth_password", "carrier_policy", "dust_start_height"} <= c.unknown)
        self.assertIn("using defaults", " ".join(c.warnings))
        c.load()                                                 # reading it again changes nothing
        self.assertTrue(c.auth_unknown)
        # what the environment or the command line sets is known whatever the file says
        e = Config(d, env={"BMV_AUTH_USER": "bob", "BMV_AUTH_PASSWORD": "pw", "BMV_CARRIER_POLICY": "false"},
                   cli={"dust_start_height": "0"})
        self.assertFalse(e.auth_unknown)
        self.assertFalse(e.unknown & {"carrier_policy", "dust_start_height"})
        self.rewrite(d, '{"auth_user": "bob", "auth_password": "pw"}')
        c.load()
        self.assertEqual(c.unknown, set())
        self.assertEqual(c.auth_user, "bob")

    def test_invalid_value_without_a_previous_one_is_unknown(self):
        d = self.settings_dir('{"auth_user": ["bob"], "auth_password": "pw", "carrier_policy": "flase", "port": "abc"}')
        c = Config(d, env={})
        self.assertEqual(c.unknown, {"auth_user", "carrier_policy", "port"})
        self.assertTrue(c.auth_unknown)
        self.assertEqual((c.auth_user, c.carrier_policy, c.port), ("", True, 8338))      # defaults meanwhile

    def test_absent_file_is_not_damaged(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        c = Config(d, env={})
        self.assertEqual(c.unknown, set())
        self.assertFalse(c.auth_unknown)
        self.rewrite(d, "{broken")                              # appears damaged later: defaults stay
        c.load()
        self.assertEqual(c.unknown, set())

    def test_damaged_file_is_not_overwritten_by_a_settings_change(self):
        d = self.settings_dir('{"auth_user": "bob", "auth_password": "pw"}')
        c = Config(d, env={})
        self.rewrite(d, '{"auth_user": "bob", "auth_password": "pw",')
        c.load()
        with self.assertRaises(PermissionError):
            c.update_file({"scan_window": "02:00-03:00"})
        with open(os.path.join(d, "settings.json")) as f:
            self.assertEqual(f.read(), '{"auth_user": "bob", "auth_password": "pw",')
        self.assertEqual(c.auth_user, "bob")

    def test_settings_file_private(self):
        d = tempfile.mkdtemp()
        try:
            c = Config(d, env={})
            c.update_file({"scan_window": "02:00-03:00"})
            self.assertEqual(os.stat(os.path.join(d, "settings.json")).st_mode & 0o777, 0o600)
            with self.assertRaises(PermissionError):
                c.update_file({"rpc_url": "http://evil:1"})
            m = Config(d, env={"BMV_MANAGED_BY": "startos"})
            with self.assertRaises(PermissionError):
                m.update_file({"scan_window": "02:00-04:00"})
            pv = {x["name"]: x for x in Config(d, env={"BMV_RPC_PASSWORD": "hunter2"}).public_view()}
            self.assertEqual(pv["rpc_password"]["value"], "********")
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()


class Trends(unittest.TestCase):
    """Pace and records for the overview, from synthetic block rows."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "x.sqlite")
        self.st = Store(self.path)
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.addCleanup(self.st.close)

    def fill(self, top, spam_at):
        cols = ",".join(BLOCK_COLS)
        rows = []
        for h in range(top + 1):
            r = dict.fromkeys(BLOCK_COLS, 0)
            r.update(height=h, hash=h.to_bytes(32, "little"), time=1600000000 + h * 600, size=1000, stored=900,
                     envelope=spam_at(h))
            rows.append(tuple(r[c] for c in BLOCK_COLS))
        self.st.begin()
        self.st.db.executemany(f"INSERT INTO blocks ({cols}) VALUES ({','.join('?' * len(BLOCK_COLS))})", rows)
        self.st.commit()

    def test_windows_and_records(self):
        top = 60000
        ya = top - 52560

        def spam(h):
            if h == 1234:
                return 900                                     # the biggest spam block
            if h > top - 1000:
                return 300                                     # last 1,000 blocks: 30 %
            if ya - 999 <= h <= ya:
                return 100                                     # same window a year earlier: 10 %
            return 50
        self.fill(top, spam)
        t = Analytics(self.path).trends()
        self.assertAlmostEqual(t["recent"]["spam_pct"], 30)
        self.assertAlmostEqual(t["year_ago"]["spam_pct"], 10)
        self.assertEqual((t["year_ago"]["from"], t["year_ago"]["to"]), (ya - 999, ya))
        month = 1000 * 300 + 3320 * 50
        self.assertEqual(t["last_month"]["blocks"], 4320)
        self.assertEqual(t["last_month"]["spam_bytes"], month)
        self.assertAlmostEqual(t["last_month"]["per_year_bytes"], month * 365 / 30)
        self.assertEqual(t["top_block"]["height"], 1234)
        self.assertAlmostEqual(t["top_block"]["spam_pct"], 90)
        months = Analytics(self.path).history()["months"]
        best = max(months, key=lambda m: m["envelope"])
        self.assertEqual((t["top_month"]["month"], t["top_month"]["spam_bytes"]), (best["month"], best["envelope"]))
        self.assertFalse(t["complete"])

    def test_half_scanned_window_is_not_a_trend(self):
        """A window counts only when 95 % of its blocks are scanned."""
        self.fill(60000, lambda h: 10)
        self.st.begin()
        self.st.db.execute("DELETE FROM blocks WHERE height BETWEEN ? AND ?", (60000 - 52560 - 999, 60000 - 52560 - 900))
        self.st.commit()
        t = Analytics(self.path).trends()
        self.assertIsNone(t["year_ago"])                      # 100 of 1,000 blocks missing
        self.assertIsNotNone(t["recent"])
        # meanwhile the dashboard names the block from which the window counts: the full scan
        # fills the 100 missing blocks upwards, and 95 % is reached at the 50th of them
        lo = 60000 - 52560 - 999
        self.assertEqual(t["pending"]["year_ago"], {"coverage": 90.0, "at": lo + 49})
        self.assertNotIn("recent", t["pending"])
        # the scan's cursor already past some of them (skipped, e.g. pruned): those will not come
        self.st.set_meta("full_next_height", str(lo + 40))
        self.assertEqual(Analytics(self.path).trends()["pending"]["year_ago"]["at"], lo + 40 + 49)
        self.st.set_meta("full_next_height", str(lo + 60))          # only 40 left, 50 needed
        self.assertIsNone(Analytics(self.path).trends()["pending"]["year_ago"]["at"])
        # after the full scan (e.g. a pruned node) the window cannot fill any more
        self.st.set_meta("full_done", "1")
        self.assertIsNone(Analytics(self.path).trends()["pending"]["year_ago"]["at"])

    def test_short_chain(self):
        self.fill(500, lambda h: 0)
        t = Analytics(self.path).trends()
        self.assertIsNone(t["last_month"])
        self.assertIsNone(t["recent"])
        self.assertIsNone(t["top_block"])                     # no spam at all: no record to show
        self.assertIsNone(t["top_month"])
        # a 501-block chain has no 30-day window yet (it would reach below genesis)
        self.assertIsNone(t["pending"]["last_month"])
        self.assertIsNone(t["pending"]["year_ago"])
        self.assertTrue(Analytics(os.path.join(self.dir, "none.sqlite")).trends()["empty"])


class QueryCache(unittest.TestCase):
    """Custom ranges and the per-block chart are cached for 3 s like the summary (L5), and the cache is bounded."""

    def test_period_and_range_cached_and_bounded(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        path = os.path.join(d, "regtest", "bmv.sqlite")
        Store(path).close()
        a = Analytics(path)
        p1, r1 = a.period(lo=0, hi=10), a.range(0, 10, 50)
        self.assertIs(a.period(lo=0, hi=10), p1)
        self.assertIs(a.range(0, 10, 50), r1)
        self.assertIsNot(a.period(lo=0, hi=11), p1)
        for i in range(500):
            a.period(lo=0, hi=100 + i)
        self.assertLessEqual(len(a._cache), 130)
        a.close()


class UtxoInfoSchedule(unittest.TestCase):
    """M1: the node is asked for its UTXO set size once a week; after a failure again after a day."""
    setUp, tearDown, config = ScannerTest.setUp, ScannerTest.tearDown, ScannerTest.config

    def test_weekly_and_a_day_after_a_failure(self):
        from unittest import mock
        from bitcoinmonetaryview import scanner as sc

        class Prepared(Scanner):
            def loop(self):
                pass

        s = Prepared(self.config(quick_pass_blocks="0", sample_every="0"))
        s.session()
        self.addCleanup(s.store.close)
        self.addCleanup(s.node.close)
        asked = lambda: sum(1 for m, *_ in list(self.node.calls) if m == "gettxoutsetinfo")
        handle, failing = self.node.handle, {"on": False}

        def maybe_fail(r):
            if failing["on"] and r.get("method") == "gettxoutsetinfo":
                self.node.calls.append(("gettxoutsetinfo", r.get("params")))
                return {"result": None, "error": {"code": -1, "message": "busy"}, "id": r.get("id")}
            return handle(r)
        self.node.handle = maybe_fail
        day, t0 = 86400, time.time()

        def at(seconds):
            with mock.patch.object(sc.time, "time", return_value=t0 + seconds):
                s.maybe_utxo_info()
            return asked()

        self.assertEqual(at(0), 1)
        self.assertEqual(at(6 * day), 1)               # not daily any more
        self.assertEqual(at(7 * day + 60), 2)          # weekly
        failing["on"] = True
        self.assertEqual(at(14 * day + 120), 3)        # fails
        self.assertEqual(at(14 * day + 120 + 2 * 3600), 3)     # no retry after an hour or two
        self.assertEqual(at(15 * day + 240), 4)        # but a day later


class TrendsReuseHistory(unittest.TestCase):
    """L10: the trends use the cached monthly history instead of running that query again."""

    def test_history_query_runs_once(self):
        from unittest import mock
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        path = os.path.join(d, "regtest", "bmv.sqlite")
        s = Store(path)
        raw, _ = bg.mixed_block(5)
        s.put_block(5, mr.analyze_block(raw, 5, dust_start_height=0), utxo_done=False)
        s.close()
        a = Analytics(path)
        self.addCleanup(a.close)
        with mock.patch.object(Analytics, "_history_in", side_effect=Analytics._history_in, autospec=True) as h:
            a.history()
            a.trends()
            self.assertEqual(h.call_count, 1)
