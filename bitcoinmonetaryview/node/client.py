# SPDX-License-Identifier: AGPL-3.0-or-later
"""High-level, read-only view of a Bitcoin Core/Knots node (RPC + optional REST)."""

import time

from ..rules import monetary_rules as mr
from .rest import RESTClient
from .rpc import RPCClient

HASHES_PER_BATCH = 500


class Node:
    def __init__(self, url, user=None, password=None, cookie_file=None,
                 proxy=None, cafile=None, timeout=120.0, use_rest=True):
        self.rpc = RPCClient(url, user=user, password=password, cookie_file=cookie_file,
                             timeout=timeout, proxy=proxy, cafile=cafile)
        self.rest = RESTClient(self.rpc.transport)
        self.use_rest = use_rest
        self.last_latency = 0.0

    @property
    def transport(self):
        return self.rpc.transport

    def probe_rest(self):
        if not self.use_rest:
            self.rest.available = False
            return False
        return self.rest.probe()

    def chain_info(self):
        return self.rpc.call("getblockchaininfo")

    def network_info(self):
        return self.rpc.call("getnetworkinfo")

    def best_hash(self):
        return self.rpc.call("getbestblockhash")

    def block_hash(self, height):
        return self.rpc.call("getblockhash", height)

    def block_hashes(self, start, count):
        """Hashes for heights start..start+count-1 (batched)."""
        out = []
        h = start
        end = start + count
        while h < end:
            n = min(HASHES_PER_BATCH, end - h)
            out.extend(self.rpc.batch([("getblockhash", [x]) for x in range(h, h + n)]))
            h += n
        return out

    def header(self, block_hash):
        return self.rpc.call("getblockheader", block_hash, True)

    def block(self, block_hash):
        """Raw serialized block bytes (REST binary if available, else hex RPC)."""
        t = time.monotonic()
        if self.rest.available:
            raw = self.rest.block(block_hash)
        else:
            raw = bytes.fromhex(self.rpc.call("getblock", block_hash, 0))
        self.last_latency = time.monotonic() - t
        return raw

    def utxo_set_info(self):
        return self.rpc.call("gettxoutsetinfo", "none")

    def close(self):
        self.rpc.close()


def network_name(chain):
    """Normalise getblockchaininfo.chain to a short name."""
    return {"main": "mainnet", "test": "testnet3", "testnet4": "testnet4",
            "signet": "signet", "regtest": "regtest"}.get(chain, chain)


def default_dust_start(network):
    """Height from which upstream's P2TR dust rule applies on this network."""
    return mr.MAINNET_DUST_START if network == "mainnet" else 0
