# SPDX-License-Identifier: AGPL-3.0-or-later
"""An in-process fake bitcoind (JSON-RPC + REST) serving synthetic blocks."""

import base64
import json
import os
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bitcoinmonetaryview.rules import monetary_rules as mr
from tests import blockgen as bg


class MockChain:
    def __init__(self):
        self.blocks = []        # [(raw, hash_bytes)]
        self.lock = threading.Lock()

    def tip_hex(self):
        return mr.hash_to_hex(self.blocks[-1][1])

    def append(self, maker=None):
        h = len(self.blocks)
        prev = self.blocks[-1][1] if self.blocks else b"\x00" * 32
        if maker is None:
            raw, bh = bg.mixed_block(h, prev=prev, seed=h)
        else:
            raw, bh = maker(h, prev)
        self.blocks.append((raw, bh))
        return bh

    def build(self, n):
        for _ in range(n):
            self.append()

    def reorg(self, depth, new_len=None):
        """Replace the last `depth` blocks with different ones (optionally longer)."""
        with self.lock:
            del self.blocks[len(self.blocks) - depth:]
            for _ in range(new_len or depth):
                h = len(self.blocks)
                prev = self.blocks[-1][1]
                raw, bh = bg.mixed_block(h, prev=prev, seed=10_000 + h)
                self.blocks.append((raw, bh))

    def find(self, hex_hash):
        hb = mr.hex_to_hash(hex_hash)
        for i, (raw, bh) in enumerate(self.blocks):
            if bh == hb:
                return i
        return None


class MockNode:
    def __init__(self, chain, user="u", password="p", cookie_path=None, rest=True,
                 ibd=False, delay=0.0, chain_name="regtest", pruned=False, prune_height=0):
        self.chain = chain
        self.user, self.password = user, password
        self.cookie_path = cookie_path
        self.rest = rest
        self.ibd = ibd
        self.delay = delay
        self.chain_name = chain_name
        self.pruned = pruned
        self.prune_height = prune_height
        self.calls = []
        self.tamper_heights = set()
        if cookie_path:
            self.rotate_cookie()
        node = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                node.calls.append(("REST", self.path))
                if not node.rest:
                    return self._send(404, b"not found", "text/plain")
                p = self.path
                if p == "/rest/chaininfo.json":
                    return self._send(200, json.dumps(node.chaininfo()).encode())
                if p.startswith("/rest/block/") and p.endswith(".bin"):
                    i = node.chain.find(p[len("/rest/block/"):-4])
                    if i is None or node.is_pruned(i):
                        return self._send(404, b"not found", "text/plain")
                    time.sleep(node.delay)
                    return self._send(200, node.raw(i), "application/octet-stream")
                if p.startswith("/rest/headers/"):
                    rest = p[len("/rest/headers/"):]
                    if "?count=" in rest:
                        hx, cnt = rest.split(".bin?count=")
                    else:
                        cnt, hx = rest[:-4].split("/")
                    i = node.chain.find(hx)
                    if i is None:
                        return self._send(404, b"not found", "text/plain")
                    hs = b"".join(r[:80] for r, _ in node.chain.blocks[i:i + int(cnt)])
                    return self._send(200, hs, "application/octet-stream")
                return self._send(404, b"not found", "text/plain")

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if not node.auth_ok(self.headers.get("Authorization", "")):
                    return self._send(401, b"")
                req = json.loads(body)
                if isinstance(req, list):
                    out = [node.handle(r) for r in req]
                else:
                    out = node.handle(req)
                self._send(200, json.dumps(out).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def rotate_cookie(self):
        self.cookie = "__cookie__:" + base64.b16encode(os.urandom(16)).decode().lower()
        with open(self.cookie_path, "w") as f:
            f.write(self.cookie)

    def auth_ok(self, header):
        if not header.startswith("Basic "):
            return False
        cred = base64.b64decode(header[6:]).decode()
        if self.cookie_path:
            return cred == self.cookie
        return cred == f"{self.user}:{self.password}"

    def is_pruned(self, i):
        return self.pruned and i < self.prune_height

    def raw(self, i):
        raw = self.chain.blocks[i][0]
        if i in self.tamper_heights:
            j = raw.find(b"D" * 120)
            raw = raw[:j] + b"E" + raw[j + 1:]
        return raw

    def chaininfo(self):
        n = len(self.chain.blocks)
        info = {"chain": self.chain_name, "blocks": n - 1, "headers": n - 1,
                "bestblockhash": self.chain.tip_hex(), "initialblockdownload": self.ibd,
                "verificationprogress": 0.5 if self.ibd else 1.0, "pruned": self.pruned,
                "size_on_disk": sum(len(r) for r, _ in self.chain.blocks)}
        if self.pruned:
            info["pruneheight"] = self.prune_height
        return info

    def handle(self, r):
        m, p, rid = r.get("method"), r.get("params", []), r.get("id")
        self.calls.append((m, p))

        def ok(x):
            return {"result": x, "error": None, "id": rid}

        def err(code, msg):
            return {"result": None, "error": {"code": code, "message": msg}, "id": rid}

        blocks = self.chain.blocks
        if m == "getblockchaininfo":
            return ok(self.chaininfo())
        if m == "getnetworkinfo":
            return ok({"version": 290000, "subversion": "/Satoshi:29.0.0/", "connections": 8})
        if m == "getbestblockhash":
            return ok(self.chain.tip_hex())
        if m == "getblockcount":
            return ok(len(blocks) - 1)
        if m == "getblockhash":
            if not 0 <= p[0] < len(blocks):
                return err(-8, "Block height out of range")
            return ok(mr.hash_to_hex(blocks[p[0]][1]))
        if m == "getblockheader":
            i = self.chain.find(p[0])
            if i is None:
                return err(-5, "Block not found")
            hdr = blocks[i][0][:80]
            return ok({"hash": p[0], "height": i, "time": struct.unpack_from("<I", hdr, 68)[0],
                       "previousblockhash": mr.hash_to_hex(hdr[4:36]) if i else None,
                       "confirmations": len(blocks) - i})
        if m == "getblock":
            i = self.chain.find(p[0])
            if i is None:
                return err(-5, "Block not found")
            if self.is_pruned(i):
                return err(-1, "Block not available (pruned data)")
            time.sleep(self.delay)
            return ok(self.raw(i).hex())
        if m == "gettxoutsetinfo":
            return ok({"height": len(blocks) - 1, "txouts": 1000, "bogosize": 100000,
                       "disk_size": 50000, "total_amount": 21.0})
        return err(-32601, "Method not found")

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
