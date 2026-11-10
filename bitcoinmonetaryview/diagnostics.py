# SPDX-License-Identifier: AGPL-3.0-or-later
"""Connection test with plain-English fix hints."""

import time

from .node.client import make_node
from .node.rpc import CookieMissing, NodeAuthError, NodeError, RPCError
from .node.transport import NodeUnreachable


def run_connection_test(config):
    """Return a list of checks: {name, status: ok|warn|fail, message, hint}."""
    checks = []

    def add(name, status, message, hint=""):
        checks.append({"name": name, "status": status, "message": message, "hint": hint})

    try:
        node = make_node(config)
    except ValueError as e:
        add("Settings", "fail", str(e), "Set rpc_url and either rpc_cookie_file or rpc_user/rpc_password.")
        return checks
    t = node.transport
    if t.plaintext_remote:
        add("Encryption", "warn", "RPC uses plain HTTP to another machine — the RPC password travels unencrypted.",
            "Use an https:// RPC URL (Start9 offers one), Tor (.onion with tor_proxy) or an SSH tunnel.")
    try:
        t0 = time.monotonic()
        info = node.chain_info()
        ms = (time.monotonic() - t0) * 1000
        add("Reachable", "ok", f"Connected to {t.describe()} ({ms:.0f} ms)")
        add("Authentication", "ok", "RPC credentials accepted")
    except CookieMissing as e:
        add("Authentication", "warn", str(e),
            "The node writes this file when it starts, so it may still be starting. If it is running, check rpc_cookie_file.")
        return checks
    except NodeAuthError as e:
        add("Authentication", "fail", str(e),
            "Check rpc_user/rpc_password, or point rpc_cookie_file to the node's .cookie file. "
            "On Start9 the RPC credentials are shown in the Bitcoin Core/Knots/Roots service's properties.")
        return checks
    except NodeUnreachable as e:
        add("Reachable", "fail", str(e),
            "Is the node running and the URL/port right? For access from another computer the node needs "
            "rpcbind=<its LAN address> (or 0.0.0.0) and rpcallowip=<your network, e.g. 192.168.1.0/24> in "
            "bitcoin.conf. On Start9, use the RPC address shown in the service's interfaces.")
        return checks
    except RPCError as e:
        add("Reachable", "warn", f"Node answered with an error: {e}",
            "The node may still be starting up. Try again in a minute.")
        return checks
    except NodeError as e:
        add("Reachable", "fail", str(e))
        return checks

    chain = info.get("chain")
    add("Network", "ok", f"Chain: {chain}, height {info.get('blocks'):,}")
    if info.get("initialblockdownload"):
        add("Sync", "warn", f"Node is still syncing ({float(info.get('verificationprogress', 0)) * 100:.1f} %)",
            "Scanning starts automatically once the node is fully synced.")
    else:
        add("Sync", "ok", "Node is fully synced")
    if info.get("pruned"):
        add("Pruning", "warn", f"Pruned node: blocks below {info.get('pruneheight', 0):,} are not available",
            "Block statistics cover the available range only; the exact spam UTXO figure needs a non-pruned node.")
    else:
        add("Pruning", "ok", "Full block history available")
    if node.probe_rest():
        add("REST", "ok", "REST interface is enabled — blocks are fetched in binary (faster)")
    else:
        # On StartOS/Umbrel the platform manages the node's config: no bitcoin.conf advice there.
        add("REST", "ok", "REST interface not enabled — using RPC (works fine, a bit slower)",
            "" if config.platform else "Optional: rest=1 in bitcoin.conf roughly halves the transfer size. Not required.")
    try:
        h = node.best_hash()
        t0 = time.monotonic()
        raw = node.block(h)
        dt = time.monotonic() - t0
        add("Throughput", "ok", f"Fetched the latest block ({len(raw) / 1e6:.2f} MB) in {dt:.2f} s "
                                f"≈ {len(raw) / 1e6 / max(dt, 1e-3):.1f} MB/s")
    except NodeError as e:
        add("Throughput", "warn", f"Could not fetch a block: {e}")
    node.close()
    return checks
