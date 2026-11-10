# SPDX-License-Identifier: AGPL-3.0-or-later
"""High-level, read-only view of a Bitcoin Core/Knots/Roots node (RPC + optional REST)."""

from ..rules import monetary_rules as mr
from .rest import RESTClient
from .rpc import NodeAuthError, NodeError, NodeUnreachable, RPCClient, RPCError

HASHES_PER_BATCH = 500


class Node:
    def __init__(self, url, user=None, password=None, cookie_file=None,
                 proxy=None, cafile=None, timeout=120.0, use_rest=True):
        self.rpc = RPCClient(url, user=user, password=password, cookie_file=cookie_file,
                             timeout=timeout, proxy=proxy, cafile=cafile)
        self.rest = RESTClient(self.rpc.transport)
        self.use_rest = use_rest
        self.batch_ok = True

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
        """Hashes for heights start..start+count-1 (batched; falls back to single calls)."""
        return self.hashes_for(range(start, start + count))

    def hashes_for(self, heights):
        """Block hashes for the given heights. Uses JSON-RPC batches; RPC proxies that
        do not support batches (e.g. some platform proxies) get one call per height."""
        heights = list(heights)
        out = []
        for i in range(0, len(heights), HASHES_PER_BATCH):
            chunk = heights[i:i + HASHES_PER_BATCH]
            if self.batch_ok:
                try:
                    out.extend(self.rpc.batch([("getblockhash", [x]) for x in chunk]))
                    continue
                except (RPCError, NodeUnreachable, NodeAuthError):
                    raise
                except NodeError:          # malformed/unsupported batch response
                    self.batch_ok = False
            out.extend(self.rpc.call("getblockhash", x) for x in chunk)
        return out

    def header(self, block_hash):
        return self.rpc.call("getblockheader", block_hash, True)

    def block(self, block_hash):
        """Raw serialized block bytes (REST binary if available, else hex RPC)."""
        if self.rest.available:
            return self.rest.block(block_hash)
        return bytes.fromhex(self.rpc.call("getblock", block_hash, 0))

    def close(self):
        self.rpc.close()


NETWORKS = {"main": "mainnet", "test": "testnet3", "testnet4": "testnet4", "signet": "signet", "regtest": "regtest"}


def make_node(config, timeout=None):
    """The node connection the settings describe (timeout: rpc_timeout unless given)."""
    c = config
    proxy = None
    if c.tor_proxy:
        host, port = c.tor_proxy.rsplit(":", 1)
        proxy = (host.strip("[]"), int(port))
    node = Node(c.rpc_url, user=c.rpc_user or None, password=c.rpc_password,
                cookie_file=c.rpc_cookie_file or None, proxy=proxy, cafile=c.rpc_cafile or None,
                timeout=timeout or c.rpc_timeout, use_rest=c.use_rest)
    # On a platform (StartOS, Umbrel) the app reaches the node over its internal network.
    node.transport.trusted_network = c.platform
    return node


def network_name(chain):
    """getblockchaininfo.chain as the short name that also names the network's data directory."""
    if chain not in NETWORKS:
        raise NodeError(f"the node reports an unsupported chain {str(chain)[:40]!r}")
    return NETWORKS[chain]


def default_dust_start(network):
    """Height from which upstream's P2TR dust rule applies on this network."""
    return mr.MAINNET_DUST_START if network == "mainnet" else 0
