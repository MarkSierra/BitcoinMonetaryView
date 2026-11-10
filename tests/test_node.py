# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest

from bitcoinmonetaryview.node import rpc as rpcmod
from bitcoinmonetaryview.node.client import Node as _Node, make_node
from bitcoinmonetaryview.node.transport import NodeUnreachable, is_local_host, socks5_connect
from bitcoinmonetaryview.rules import monetary_rules as mr
from tests.mocknode import MockChain, MockNode

_open_nodes = []


class Node(_Node):
    """The app's client; tests close every instance afterwards (see tearDownModule)."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _open_nodes.append(self)


def tearDownModule():
    for n in _open_nodes:
        n.close()


class NetworkName(unittest.TestCase):
    def test_known_chains(self):
        from bitcoinmonetaryview.node.client import network_name
        self.assertEqual([network_name(c) for c in ("main", "test", "testnet4", "signet", "regtest")],
                         ["mainnet", "testnet3", "testnet4", "signet", "regtest"])

    def test_other_names_are_refused(self):
        """The name becomes a directory name: it must never point outside the data directory."""
        from bitcoinmonetaryview.node.client import network_name
        for chain in ("../../x", "mainnet", "", None, "x/y", "test2"):
            with self.assertRaises(rpcmod.NodeError, msg=repr(chain)):
                network_name(chain)

    def test_scanner_writes_nothing_for_an_unknown_chain(self):
        from bitcoinmonetaryview.config import Config
        from bitcoinmonetaryview.scanner import Scanner
        import time
        chain = MockChain()
        chain.build(3)
        node = MockNode(chain, chain_name="../escaped")
        d = tempfile.mkdtemp()
        data = os.path.join(d, "data")
        os.mkdir(data)                  # as __main__ does before the scanner starts
        try:
            s = Scanner(Config(data, cli={"rpc_url": node.url, "rpc_user": "u", "rpc_password": "p"}, env={}))
            s.start()
            t = time.time()
            while s.status.get("phase") != "error" and time.time() - t < 20:
                time.sleep(0.05)
            s.stop()
            s.join(10)
            self.assertEqual(s.status.get("phase"), "error")
            self.assertIn("chain", s.status.get("detail"))
            self.assertEqual(sorted(os.listdir(d)), ["data"])
            self.assertEqual(os.listdir(data), [])
        finally:
            node.stop()
            shutil.rmtree(d)


class Whitelist(unittest.TestCase):
    def test_forbidden_methods_refused_locally(self):
        for m in ("stop", "dumptxoutset", "invalidateblock", "sendtoaddress", "walletpassphrase",
                  "setban", "pruneblockchain", "importdescriptors", "createwallet", "savemempool",
                  "submitblock", "loadtxoutset", "help", "", None):
            with self.assertRaises(rpcmod.RPCForbidden, msg=m):
                rpcmod.check_allowed(m, [])

    def test_forbidden_params_refused(self):
        h = "00" * 32
        bad = [("getblock", [h, 1]), ("getblock", [h, 2]), ("getblock", [h, True]), ("getblock", [h]),
               ("getblock", ["zz" * 32, 0]), ("gettxoutsetinfo", []), ("gettxoutsetinfo", ["muhash"]),
               ("gettxoutsetinfo", ["none", 5]), ("getblockhash", [-1]), ("getblockhash", ["1"]),
               ("getblockhash", [True]), ("getblockchaininfo", [1])]
        for m, p in bad:
            with self.assertRaises(rpcmod.RPCForbidden, msg=(m, p)):
                rpcmod.check_allowed(m, p)

    def test_allowed(self):
        h = "ab" * 32
        for m, p in [("getblock", [h, 0]), ("getblockhash", [5]), ("getblockheader", [h, True]),
                     ("gettxoutsetinfo", ["none"]), ("getblockchaininfo", [])]:
            rpcmod.check_allowed(m, p)

    def test_batch_with_forbidden_element_sends_nothing(self):
        chain = MockChain()
        chain.build(3)
        node = MockNode(chain)
        try:
            c = rpcmod.RPCClient(node.url, "u", "p")
            with self.assertRaises(rpcmod.RPCForbidden):
                c.batch([("getblockhash", [0]), ("stop", [])])
            self.assertEqual(node.calls, [])
        finally:
            node.stop()


class Client(unittest.TestCase):
    def setUp(self):
        self.chain = MockChain()
        self.chain.build(5)

    def test_close_reaches_every_threads_connection(self):
        # the scanner fetches with a thread pool; each thread keeps its own keep-alive connection
        import concurrent.futures
        node = MockNode(self.chain)
        try:
            n = Node(node.url, "u", "p")
            barrier = threading.Barrier(3)

            def fetch(_):
                barrier.wait(10)                  # three threads at once -> three connections
                return n.chain_info()["blocks"]
            with concurrent.futures.ThreadPoolExecutor(3) as pool:
                self.assertEqual(list(pool.map(fetch, range(3))), [4, 4, 4])
            conns = list(n.rpc.transport._conns)
            self.assertEqual(len(conns), 3)
            self.assertTrue(all(c.sock is not None for c in conns))
            n.close()                             # from another thread than the ones that opened them
            self.assertTrue(all(c.sock is None for c in conns))
            self.assertEqual(n.chain_info()["blocks"], 4)   # still usable: reconnects
        finally:
            node.stop()

    def test_rpc_and_rest_blocks_equal(self):
        node = MockNode(self.chain)
        try:
            n = Node(node.url, "u", "p")
            h = n.block_hash(3)
            raw_rpc = n.block(h)
            self.assertTrue(n.probe_rest())
            raw_rest = n.block(h)
            self.assertEqual(raw_rpc, raw_rest)
            self.assertEqual(n.block_hashes(0, 5), [mr.hash_to_hex(b[1]) for b in self.chain.blocks])
        finally:
            node.stop()

    def test_batch_unsupported_falls_back(self):
        node = MockNode(self.chain)
        orig = node.handle
        node.handle = lambda r: orig(r)        # single calls fine
        try:
            n = Node(node.url, "u", "p")
            n.rpc.batch = lambda calls: (_ for _ in ()).throw(rpcmod.NodeError("unexpected batch RPC response"))
            self.assertEqual(n.block_hashes(0, 5), [mr.hash_to_hex(b[1]) for b in self.chain.blocks])
            self.assertFalse(n.batch_ok)
        finally:
            node.stop()

    def test_rest_disabled_falls_back(self):
        node = MockNode(self.chain, rest=False)
        try:
            n = Node(node.url, "u", "p")
            self.assertFalse(n.probe_rest())
            self.assertEqual(len(n.block(n.block_hash(2))), len(self.chain.blocks[2][0]))
        finally:
            node.stop()

    def test_no_bitcoin_conf_advice_on_platforms(self):
        # StartOS and Umbrel manage the node's config: the connection test must not suggest editing it
        from bitcoinmonetaryview.config import Config
        from bitcoinmonetaryview.diagnostics import run_connection_test
        node = MockNode(self.chain, rest=False)
        d = tempfile.mkdtemp()
        try:
            cli = {"rpc_url": node.url, "rpc_user": "u", "rpc_password": "p"}
            for env, advice in (({}, True), ({"BMV_MANAGED_BY": "startos"}, False), ({"BMV_MANAGED_BY": "umbrel"}, False)):
                rest = [c for c in run_connection_test(Config(d, cli=cli, env=env)) if c["name"] == "REST"][0]
                self.assertEqual(rest["status"], "ok")
                self.assertEqual("bitcoin.conf" in rest["hint"], advice, env)
        finally:
            node.stop()
            shutil.rmtree(d)

    def test_bad_password(self):
        node = MockNode(self.chain)
        try:
            n = Node(node.url, "u", "wrong")
            with self.assertRaises(rpcmod.NodeAuthError):
                n.chain_info()
        finally:
            node.stop()

    def test_cookie_rotation(self):
        d = tempfile.mkdtemp()
        try:
            cookie = os.path.join(d, ".cookie")
            node = MockNode(self.chain, cookie_path=cookie)
            n = Node(node.url, cookie_file=cookie)
            n.chain_info()
            node.rotate_cookie()            # simulated bitcoind restart
            self.assertEqual(n.chain_info()["chain"], "regtest")
            node.stop()
        finally:
            shutil.rmtree(d)

    def test_cookie_read_only(self):
        d = tempfile.mkdtemp()
        try:
            cookie = os.path.join(d, ".cookie")
            with open(cookie, "w") as f:
                f.write("__cookie__:abc")
            os.chmod(cookie, 0o400)
            before = os.stat(cookie)
            self.assertEqual(rpcmod.read_cookie(cookie), "__cookie__:abc")
            after = os.stat(cookie)
            self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)
            self.assertEqual(os.listdir(d), [".cookie"])
        finally:
            shutil.rmtree(d)

    def test_unreachable(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        n = Node(f"http://127.0.0.1:{port}", "u", "p", timeout=2)
        with self.assertRaises(NodeUnreachable):
            n.chain_info()

    def test_credentials_not_in_repr(self):
        n = Node("http://127.0.0.1:1", "alice", "s3cret")
        self.assertNotIn("s3cret", repr(n.rpc))
        with self.assertRaises(ValueError):
            Node("http://alice:s3cret@127.0.0.1:1", "a", "b")

    def test_plaintext_warning_only_for_real_networks(self):
        from bitcoinmonetaryview.config import Config
        from bitcoinmonetaryview.scanner import Scanner
        d = tempfile.mkdtemp()
        try:
            cli = {"rpc_url": "http://10.0.3.1:58332", "rpc_user": "u", "rpc_password": "p"}
            plain = make_node(Config(d, cli=cli, env={}))
            self.assertTrue(plain.transport.plaintext_remote)
            managed = make_node(Config(d, cli=cli, env={"BMV_MANAGED_BY": "startos"}))
            self.assertFalse(managed.transport.plaintext_remote)
        finally:
            shutil.rmtree(d)

    def test_local_host_detection(self):
        for h in ("127.0.0.1", "localhost", "::1", "bitcoind.startos", "bitcoind.embassy"):
            self.assertTrue(is_local_host(h), h)
        for h in ("192.168.1.5", "mynode.local", "example.com"):
            self.assertFalse(is_local_host(h), h)


class Socks5(unittest.TestCase):
    def test_hostname_sent_unresolved(self):
        """A fake SOCKS5 proxy must receive the domain name, not a resolved IP."""
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        seen = {}

        def proxy():
            c, _ = srv.accept()
            c.recv(3)
            c.sendall(b"\x05\x00")
            req = c.recv(300)
            seen["atyp"] = req[3]
            seen["host"] = req[5:5 + req[4]].decode()
            c.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
            c.close()

        t = threading.Thread(target=proxy, daemon=True)
        t.start()
        s = socks5_connect("127.0.0.1", srv.getsockname()[1], "abcdefghijklmnop.onion", 8332, 5)
        s.close()
        t.join(5)
        srv.close()
        self.assertEqual(seen, {"atyp": 3, "host": "abcdefghijklmnop.onion"})


@unittest.skipUnless(shutil.which("openssl"), "openssl CLI not available")
class TLSWithCustomCA(unittest.TestCase):
    def test_custom_ca(self):
        import http.server
        import ssl
        d = tempfile.mkdtemp()
        try:
            def run(*a):
                subprocess.run(["openssl", *a], cwd=d, check=True, capture_output=True)
            # Proper extensions: Python 3.13+ verifies with VERIFY_X509_STRICT
            run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem",
                "-days", "2", "-subj", "/CN=Test Root CA",
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                "-addext", "subjectKeyIdentifier=hash")
            run("req", "-newkey", "rsa:2048", "-nodes", "-keyout", "srv.key", "-out", "srv.csr",
                "-subj", "/CN=localhost")
            with open(os.path.join(d, "ext.cnf"), "w") as f:
                f.write("subjectAltName=DNS:localhost\nbasicConstraints=critical,CA:FALSE\n"
                        "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
                        "authorityKeyIdentifier=keyid\nsubjectKeyIdentifier=hash\n")
            run("x509", "-req", "-in", "srv.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
                "-out", "srv.pem", "-days", "2", "-extfile", "ext.cnf")

            chain = MockChain()
            chain.build(2)
            node = MockNode(chain)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(os.path.join(d, "srv.pem"), os.path.join(d, "srv.key"))
            node.server.socket = ctx.wrap_socket(node.server.socket, server_side=True)
            url = f"https://localhost:{node.port}"
            try:
                with self.assertRaises(NodeUnreachable):
                    Node(url, "u", "p", timeout=5).chain_info()       # unknown CA -> refused
                n = Node(url, "u", "p", cafile=os.path.join(d, "ca.pem"), timeout=5)
                self.assertEqual(n.chain_info()["chain"], "regtest")
            finally:
                node.stop()
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
