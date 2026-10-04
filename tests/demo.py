# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Demo: run the app against a fake node with a synthetic multi-year chain.

    python3 -m tests.demo [--blocks 600] [--port 8338] [--delay 0.01]

For development and screenshots only. Talks to nothing but the local fake node.
"""

import argparse
import random
import shutil
import tempfile
import threading

from tests import blockgen as bg
from tests.mocknode import MockChain, MockNode

START, END = 1357000000, 1790000000   # 2013-01 .. 2026-09


def era_maker(n_blocks, seed=1):
    rnd = random.Random(seed)

    def maker(h, prev):
        ts = int(START + (END - START) * h / max(1, n_blocks - 1))
        year = 2013 + (ts - START) / 31557600
        txs = []
        for i in range(rnd.randint(8, 30)):
            txs.append(bg.tx([(bg.fake_prev(h * 1000 + i), 0, b"\x00" * 107)],
                             [(rnd.randint(10_000, 10**8), bg.p2wpkh(b"%d-%d" % (h, i))),
                              (rnd.randint(10_000, 10**7), bg.p2wpkh(b"c%d-%d" % (h, i)))]))
        if 2014 <= year < 2017 and rnd.random() < 0.5:
            txs.append(bg.tx([(bg.fake_prev(h * 1000 + 900), 0, b"\x00" * 107)], [(7800, bg.stamp_multisig())]))
        if year >= 2016 and rnd.random() < 0.6:
            txs.append(bg.tx([(bg.fake_prev(h * 1000 + 901), 0, b"\x00" * 107)], [(0, bg.op_return(40))]))
        if year >= 2023:
            for i in range(rnd.randint(2, 9) if year < 2025.5 else rnd.randint(0, 4)):
                txs.append(bg.tx([(bg.fake_prev(h * 1000 + 910 + i), 0, b"")], [(546, bg.p2tr(b"ins%d-%d" % (h, i)))],
                                 witnesses=[bg.inscription_witness(rnd.randint(300, 6000))]))
        if year >= 2023.2 and rnd.random() < 0.4:
            txs.append(bg.tx([(bg.fake_prev(h * 1000 + 950), 0, b"\x00" * 107)],
                             [(7800, bg.stamp_multisig()), (5000, bg.p2wpkh(b"s%d" % h))]))
        if year >= 2025.8 and rnd.random() < 0.5:
            txs.append(bg.tx([(bg.fake_prev(h * 1000 + 960), 0, b"\x00" * 107)],
                             [(0, bg.op_return(rnd.randint(90, 400))), (5000, bg.p2wpkh(b"o%d" % h))]))
        if rnd.random() < 0.02:
            txs.append(bg.tx([(bg.fake_prev(h * 1000 + 970), 0, b"\x01" * 1700)], [(9000, bg.p2wpkh(b"d%d" % h))]))
        rnd.shuffle(txs)
        return bg.block(txs, h, prev, timestamp=ts)
    return maker


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=600)
    ap.add_argument("--port", type=int, default=8338)
    ap.add_argument("--delay", type=float, default=0.01)
    ap.add_argument("--profile", default="full")
    args = ap.parse_args()
    chain = MockChain()
    mk = era_maker(args.blocks)
    for _ in range(args.blocks):
        chain.append(mk)
    node = MockNode(chain, chain_name="main", delay=args.delay)
    d = tempfile.mkdtemp(prefix="bmv-demo-")
    print(f"fake node at {node.url}, data dir {d}")
    from bitcoinmonetaryview.__main__ import main as app_main
    try:
        app_main(["--data-dir", d, "--rpc-url", node.url, "--rpc-user", "u", "--rpc-password", "p",
                  "--port", str(args.port), "--dust-start-height", "0", "--speed-profile", args.profile,
                  "--quick-pass-blocks", "50"])
    finally:
        node.stop()
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
