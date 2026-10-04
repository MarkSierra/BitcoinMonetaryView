# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Consensus-valid spam transactions for a regtest node, buildable without a wallet.

Coinbase outputs go to raw(OP_TRUE), which can be spent with an empty scriptSig.
The inscription-style envelope uses P2WSH (valid without signatures):
witness = [<dummy>, script], script = OP_DROP OP_FALSE OP_IF <data pushes> OP_ENDIF OP_TRUE.
(Upstream only inspects P2WSH witnesses with two or more items.)
"""

import hashlib

from tests import blockgen as bg

OP_TRUE_SPK = b"\x51"


def envelope_witness_script(n_pushes=4, push_len=500):
    s = b"\x75\x00\x63" + bg.push(b"ord") + bg.push(b"\x01") + bg.push(b"text/plain") + b"\x00"
    for i in range(n_pushes):
        s += bg.push(bytes([65 + i]) * push_len)
    return s + b"\x68\x51"


def p2wsh(script):
    return b"\x00\x20" + hashlib.sha256(script).digest()


def spam_tx(coinbase_txid, coinbase_value):
    """Spends an OP_TRUE coinbase output; creates P2WSH(envelope), big OP_RETURN, stamp, P2TR dust."""
    ws = envelope_witness_script()
    fee = 100_000
    outs = [
        (100_000, p2wsh(ws)),
        (0, bg.op_return(120)),
        (7_800, bg.stamp_multisig()),
        (546, bg.p2tr(b"regtest-dust")),
    ]
    change = coinbase_value - sum(v for v, _ in outs) - fee
    outs.append((change, OP_TRUE_SPK))
    raw, txid, _ = bg.tx([(coinbase_txid, 0, b"")], outs)
    return raw, txid, ws


def reveal_tx(prev_txid, ws, value=100_000):
    """Spends the P2WSH envelope output (vout 0) -> witness carries the envelope."""
    raw, txid, wtxid = bg.tx([(prev_txid, 0, b"")], [(value - 10_000, OP_TRUE_SPK)], witnesses=[[b"\x01", ws]])
    return raw, txid
