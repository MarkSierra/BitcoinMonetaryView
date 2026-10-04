# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Integration test against a real bitcoind in regtest.

Runs when a `bitcoind` binary is available (PATH or BMV_BITCOIND); CI downloads the
official release. Skipped otherwise. The test drives its own throw-away regtest node
(mining, invalidateblock); the app under test only ever uses read-only RPC.
"""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request
import base64

from bitcoinmonetaryview.config import Config
from bitcoinmonetaryview.rules import monetary_rules as mr
from bitcoinmonetaryview.scanner import Scanner
from bitcoinmonetaryview.analytics import Analytics
from tests import blockgen as bg
from tests import regtest_txs as rt

BITCOIND = os.environ.get("BMV_BITCOIND") or shutil.which("bitcoind")


class OfflineTxCheck(unittest.TestCase):
    """The regtest transactions are classified as intended (runs everywhere)."""

    def test_txs_classified(self):
        cb_txid = b"\x01" * 32
        raw1, txid1, ws = rt.spam_tx(cb_txid, 50 * 10**8)
        raw2, _ = rt.reveal_tx(txid1, ws)
        blk, _ = bg.block([(raw1, txid1, txid1)], 101)
        r1 = mr.analyze_block(blk, 101, dust_start_height=0)
        self.assertGreater(r1["op_return"], 0)
        self.assertGreater(r1["multisig"], 0)
        self.assertEqual(r1["dust_outputs"], 1)
        tx2 = bg.tx([(txid1, 0, b"")], [(90_000, rt.OP_TRUE_SPK)], witnesses=[[b"\x01", ws]])
        blk2, _ = bg.block([tx2], 102)
        r2 = mr.analyze_block(blk2, 102, dust_start_height=0)
        self.assertGreaterEqual(r2["envelope"], 2000)
        mr.verify_block(r2)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@unittest.skipUnless(BITCOIND, "bitcoind not available (set BMV_BITCOIND to run)")
class RegtestNode(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.datadir = os.path.join(self.dir, "node")
        os.makedirs(self.datadir)
        self.rpcport = free_port()
        self.proc = subprocess.Popen([
            BITCOIND, "-regtest", f"-datadir={self.datadir}", f"-rpcport={self.rpcport}",
            f"-port={free_port()}", "-rest=1", "-listen=0", "-server=1", "-txindex=0",
            "-fallbackfee=0.0001", "-disablewallet=1", "-printtoconsole=0"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.cookie = os.path.join(self.datadir, "regtest", ".cookie")
        t = time.time()
        while time.time() - t < 60:
            try:
                self.cli("getblockchaininfo")
                break
            except Exception:
                time.sleep(0.3)
        else:
            self.fail("bitcoind did not start")
        self.scanner = None

    def tearDown(self):
        if self.scanner:
            self.scanner.stop()
            self.scanner.join(15)
        try:
            self.cli("stop")
        except Exception:
            pass
        self.proc.wait(30)
        shutil.rmtree(self.dir, ignore_errors=True)

    # test-harness RPC (NOT the app's client; this one mines and invalidates on the test node)
    def cli(self, method, *params):
        with open(self.cookie) as f:
            cred = f.read().strip()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.rpcport}/", data=json.dumps(
                {"jsonrpc": "1.0", "id": 1, "method": method, "params": list(params)}).encode(),
            headers={"Authorization": "Basic " + base64.b64encode(cred.encode()).decode(),
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
        if resp.get("error"):
            raise RuntimeError(resp["error"])
        return resp["result"]

    def wait_live(self, height, timeout=60):
        t = time.time()
        while time.time() - t < timeout:
            st = self.scanner.status.snapshot()
            if st.get("phase") == "live" and st.get("tip") == height:
                return
            time.sleep(0.2)
        self.fail(f"scanner not live at {height}: {self.scanner.status.snapshot()}")

    def test_scan_real_node(self):
        hashes = self.cli("generatetodescriptor", 101, "raw(51)")
        cb = self.cli("getblock", hashes[0], 2)["tx"][0]
        cb_txid = bytes.fromhex(cb["txid"])[::-1]
        cb_value = round(cb["vout"][0]["value"] * 1e8)
        raw1, txid1, ws = rt.spam_tx(cb_txid, cb_value)
        self.cli("generateblock", "raw(51)", [raw1.hex()])             # height 102
        raw2, _ = rt.reveal_tx(txid1, ws)
        self.cli("generateblock", "raw(51)", [raw2.hex()])             # height 103

        cfg = Config(os.path.join(self.dir, "app"), cli={
            "rpc_url": f"http://127.0.0.1:{self.rpcport}", "rpc_cookie_file": self.cookie,
            "speed_profile": "full", "quick_pass_blocks": "0", "bloom_mb": "8"}, env={})
        self.scanner = Scanner(cfg)
        self.scanner.start()
        self.wait_live(103)
        self.assertTrue(self.scanner.status.get("node")["rest"])
        a = Analytics(os.path.join(cfg.data_dir, "regtest", "bmv.sqlite"))
        b102, b103 = a.block(102), a.block(103)
        self.assertGreater(b102["op_return"], 0)
        self.assertGreater(b102["multisig"], 0)
        self.assertEqual(b102["dust_outputs"], 1)
        self.assertGreaterEqual(b103["envelope"], 2000)
        self.assertEqual(a.summary()["utxo"]["count"], 2)        # stamp + dust

        # real reorg on the test node: replace block 102 and 103
        self.cli("invalidateblock", self.cli("getblockhash", 102))
        self.cli("generatetodescriptor", 3, "raw(51)")                # new tip 104, no spam
        self.wait_live(104)
        time.sleep(0.5)
        a2 = Analytics(os.path.join(cfg.data_dir, "regtest", "bmv.sqlite"))
        self.assertEqual(a2.block(102)["op_return"], 0)
        self.assertEqual(a2.summary()["utxo"]["count"], 0)


if __name__ == "__main__":
    unittest.main()
