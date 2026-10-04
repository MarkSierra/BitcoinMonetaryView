# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import random
import subprocess
import sys
import unittest

from bitcoinmonetaryview.rules import monetary_rules as mr
from bitcoinmonetaryview.rules.upstream import carrier_policy as cp
from bitcoinmonetaryview.rules.upstream import monetary_store as up
from tests import blockgen as bg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class UpstreamSelfTests(unittest.TestCase):
    """Upstream's own test suites must pass against the vendored copies."""

    def test_upstream_monetary_store_suite(self):
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "bitcoinmonetaryview", "rules", "upstream"))
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "upstream", "test_monetary_store.py")],
                           env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertIn("0 failed", r.stdout)

    def test_upstream_carrier_policy_selftest(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "bitcoinmonetaryview", "rules", "upstream",
                                                         "carrier_policy.py"), "--selftest"],
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])


class ParityWithUpstream(unittest.TestCase):
    """analyze_block must reproduce upstream strip_block exactly (policy off, mainnet era)."""

    def check_parity(self, raw, height):
        record, st = up.strip_block(raw, height, 83, 1650)
        res = mr.analyze_block(raw, height, build_record=True)
        self.assertEqual(res["record"], record)
        self.assertEqual(res["stored"], st["stored"])
        self.assertEqual(res["original"], st["original"])
        for k in ("whole", "modified", "stripped", "dust_outputs", "envelope",
                  "op_return", "multisig", "scriptsig", "filter_entries"):
            self.assertEqual(res[k], st[k], k)
        self.assertEqual(res["txids"], st["txids"])

    def test_mixed_blocks(self):
        for seed in range(5):
            raw, _ = bg.mixed_block(800000 + seed, seed=seed)
            self.check_parity(raw, 800000 + seed)

    def test_pre_inscription_era(self):
        raw, _ = bg.mixed_block(700000)
        self.check_parity(raw, 700000)
        self.assertEqual(mr.analyze_block(raw, 700000)["dust_outputs"], 0)

    def test_random_blocks(self):
        rnd = random.Random(42)
        makers = [
            lambda i: bg.tx([(bg.fake_prev(i), 0, b"")], [(rnd.randint(1, 10**8), bg.p2wpkh(b"%d" % i))]),
            lambda i: bg.tx([(bg.fake_prev(i), 0, b"")], [(rnd.choice([330, 546, 999, 1000, 5000]), bg.p2tr(b"%d" % i))],
                            witnesses=[bg.inscription_witness(rnd.randint(1, 3000))]),
            lambda i: bg.tx([(bg.fake_prev(i), 0, b"\x00" * rnd.randint(0, 2000))],
                            [(0, bg.op_return(rnd.randint(1, 300))), (600, bg.p2tr(b"x%d" % i))]),
            lambda i: bg.tx([(bg.fake_prev(i), 0, b"")], [(7800, bg.stamp_multisig()), (1000, bg.real_multisig())]),
        ]
        for b in range(10):
            txs = [rnd.choice(makers)(b * 100 + i) for i in range(rnd.randint(1, 30))]
            raw, _ = bg.block(txs, 800000 + b)
            self.check_parity(raw, 800000 + b)


class Carriers(unittest.TestCase):
    def setUp(self):
        self.raw, self.hash = bg.mixed_block(800000)
        self.res = mr.analyze_block(self.raw, 800000)

    def test_every_carrier_detected(self):
        r = self.res
        self.assertGreaterEqual(r["envelope"], 1200)
        self.assertGreater(r["op_return"], 120)
        self.assertGreater(r["multisig"], 0)
        self.assertEqual(r["scriptsig"], 1700)
        self.assertEqual(r["dust_outputs"], 1)
        self.assertEqual(r["stripped"], 1)
        self.assertLess(r["stored"], r["original"])

    def test_utxo_effects(self):
        kinds = sorted(u[3] for u in self.res["utxo_spam"])
        # stamp multisig enters the UTXO set; the OP_RETURN does not; postage is dust
        self.assertEqual(kinds, [mr.UTXO_KIND_DATA_KEY, mr.UTXO_KIND_P2TR_DUST])
        self.assertEqual(len(self.res["spends"]), 6)

    def test_verify_ok(self):
        self.assertTrue(mr.verify_block(self.res, expected_hash=self.hash))

    def test_dust_start_parameter(self):
        res = mr.analyze_block(self.raw, 100, dust_start_height=0)
        self.assertEqual(res["dust_outputs"], 1)
        res = mr.analyze_block(self.raw, 100)
        self.assertEqual(res["dust_outputs"], 0)


class Integrity(unittest.TestCase):
    def test_wrong_hash(self):
        raw, h = bg.mixed_block(800000)
        res = mr.analyze_block(raw, 800000)
        with self.assertRaises(mr.BlockInvalid):
            mr.verify_block(res, expected_hash=b"\x11" * 32)

    def test_tampered_tx_data(self):
        raw, h = bg.mixed_block(800000)
        i = raw.find(b"D" * 120)
        bad = raw[:i] + b"E" + raw[i + 1:]
        with self.assertRaises(mr.BlockInvalid):
            mr.verify_block(mr.analyze_block(bad, 800000), expected_hash=h)

    def test_tampered_witness(self):
        raw, h = bg.mixed_block(800000)
        i = raw.find(b"I" * 100)
        bad = raw[:i] + b"J" + raw[i + 1:]
        res = mr.analyze_block(bad, 800000)
        # txids (and so the merkle root and header hash) are unchanged ...
        self.assertEqual(mr.block_hash(res["header"]), h)
        # ... but the witness commitment catches it
        with self.assertRaisesRegex(mr.BlockInvalid, "witness"):
            mr.verify_block(res, expected_hash=h)

    def test_wrong_parent(self):
        raw, h = bg.mixed_block(800000, prev=b"\x22" * 32)
        res = mr.analyze_block(raw, 800000)
        mr.verify_block(res, expected_prev=b"\x22" * 32)
        with self.assertRaises(mr.BlockInvalid):
            mr.verify_block(res, expected_prev=b"\x33" * 32)

    def test_fuzz_never_crashes(self):
        rnd = random.Random(7)
        raw, _ = bg.mixed_block(800000)
        for _ in range(400):
            b = bytearray(raw)
            op = rnd.random()
            if op < 0.4:
                b = b[:rnd.randint(0, len(b))]
            elif op < 0.8:
                for _ in range(rnd.randint(1, 20)):
                    b[rnd.randrange(len(b))] = rnd.randrange(256)
            else:
                b = bytearray(rnd.randbytes(rnd.randint(0, 3000)))
            try:
                res = mr.analyze_block(bytes(b), 800000)
                mr.verify_block(res)
            except mr.BlockInvalid:
                pass


class RealBlocks(unittest.TestCase):
    """Real blocks from Bitcoin Core's own test/bench data (MIT, see NOTICE)."""

    FIX = os.path.join(ROOT, "tests", "fixtures")

    def test_mainnet_413567(self):
        raw = open(os.path.join(self.FIX, "block413567.raw"), "rb").read()
        res = mr.analyze_block(raw, 413567, build_record=True)
        mr.verify_block(res, expected_hash=mr.hex_to_hash(
            "0000000000000000025aff8be8a55df8f89c77296db6198f272d6577325d4069"))
        self.assertEqual(res["tx_count"], 1557)
        record, st = up.strip_block(raw, 413567, 83, 1650)
        self.assertEqual(res["record"], record)

    def test_testnet_blocks_incl_witness(self):
        import json
        rows = json.load(open(os.path.join(self.FIX, "blockfilters.json")))[1:]
        seen_witness = False
        for height, hexhash, hexblock, *_ in rows:
            raw = bytes.fromhex(hexblock)
            res = mr.analyze_block(raw, height, dust_start_height=0)
            mr.verify_block(res, expected_hash=mr.hex_to_hash(hexhash))
            seen_witness |= res["has_witness"]
            record, _ = up.strip_block(raw, height, 83, 1650)
            self.assertEqual(mr.analyze_block(raw, height, build_record=True)["record"], record)
        self.assertTrue(seen_witness)


class CarrierPolicy(unittest.TestCase):
    def test_shielded_envelope_retained_only_with_policy(self):
        env = bg_envelope()
        spk = cp.encode_op_return(env)
        k_off = mr.classify_output(spk, 0, 900000, 83)
        k_on = mr.classify_output(spk, 0, 900000, 83, carrier_policy=cp.Policy())
        self.assertEqual(k_off[0], "spam")
        self.assertEqual(k_on[0], "monetary")
        self.assertEqual(k_on[1], "retained_protocol")

    def test_policy_does_not_change_size_rule(self):
        pol = cp.Policy()
        for n in (1, 40, 80, 81, 82, 83, 84, 100, 200):
            spk = bg.op_return(n)
            self.assertEqual(mr.classify_output(spk, 0, 900000, 83)[0],
                             mr.classify_output(spk, 0, 900000, 83, carrier_policy=pol)[0], n)

    def test_jpeg_with_prefix_not_retained(self):
        env = bg_envelope()
        spk = cp.encode_op_return(env + b"\xff\xd8 jpeg")
        self.assertEqual(mr.classify_output(spk, 0, 900000, 83, carrier_policy=cp.Policy())[0], "spam")


def bg_envelope():
    return cp._envelope(n_in=2, n_out=2)


if __name__ == "__main__":
    unittest.main()
