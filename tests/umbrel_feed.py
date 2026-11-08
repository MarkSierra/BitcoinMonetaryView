# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Mines a small regtest chain with spam on a node reached over RPC (CI only: the umbrelOS test).

Usage: python -m tests.umbrel_feed <rpc-url> <user> <password>
Prints the final height and one spam block height as `height=…` and `spam_height=…`.
"""

import base64
import json
import sys
import time
import urllib.request

from tests import regtest_txs as rt

SPAM_BLOCKS = 20


def rpc(url, auth, method, *params):
    req = urllib.request.Request(url, data=json.dumps(
        {"jsonrpc": "1.0", "id": 1, "method": method, "params": list(params)}).encode(),
        headers={"Authorization": "Basic " + auth, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read())
    if resp.get("error"):
        raise RuntimeError(resp["error"])
    return resp["result"]


def main(url, user, password):
    auth = base64.b64encode(f"{user}:{password}".encode()).decode()
    deadline = time.time() + 600
    while True:
        try:
            info = rpc(url, auth, "getblockchaininfo")
            if info["chain"] == "regtest":
                break
            print(f"node is on {info['chain']}, waiting for regtest", flush=True)
        except Exception as e:  # node still starting
            print(f"waiting for the node: {e}", flush=True)
        if time.time() > deadline:
            sys.exit("node did not come up on regtest")
        time.sleep(5)

    hashes = rpc(url, auth, "generatetodescriptor", 100 + SPAM_BLOCKS, "raw(51)")
    spam_height = None
    for h in hashes[:SPAM_BLOCKS]:
        cb = rpc(url, auth, "getblock", h, 2)["tx"][0]
        raw1, txid1, ws = rt.spam_tx(bytes.fromhex(cb["txid"])[::-1], round(cb["vout"][0]["value"] * 1e8))
        rpc(url, auth, "generateblock", "raw(51)", [raw1.hex()])
        raw2, _ = rt.reveal_tx(txid1, ws)
        rpc(url, auth, "generateblock", "raw(51)", [raw2.hex()])
        spam_height = spam_height or rpc(url, auth, "getblockcount") - 1
    rpc(url, auth, "generatetodescriptor", 30, "raw(51)")
    print(f"height={rpc(url, auth, 'getblockcount')}")
    print(f"spam_height={spam_height}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
