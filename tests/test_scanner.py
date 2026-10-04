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
from bitcoinmonetaryview.store import Bloom
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
