# SPDX-License-Identifier: AGPL-3.0-or-later
"""Synthetic block builder for tests: real merkle roots and witness commitments."""

import hashlib
import os
import struct

from bitcoinmonetaryview.rules.upstream.monetary_store import (
    SECP_P, dsha, merkle_root, write_varint)

# A real secp256k1 point (generator G, compressed).
REAL_KEY = bytes.fromhex(
    "0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798")


def push(data):
    n = len(data)
    if n < 0x4C:
        return bytes([n]) + data
    if n <= 0xFF:
        return b"\x4c" + bytes([n]) + data
    if n <= 0xFFFF:
        return b"\x4d" + struct.pack("<H", n) + data
    return b"\x4e" + struct.pack("<I", n) + data


def off_curve_x():
    """A 32-byte x with no point on secp256k1."""
    x = 5
    while True:
        y2 = (pow(x, 3, SECP_P) + 7) % SECP_P
        if pow(y2, (SECP_P - 1) // 2, SECP_P) != 1:
            return x.to_bytes(32, "big")
        x += 1


def p2tr(seed=b"k"):
    return b"\x51\x20" + hashlib.sha256(seed).digest()


def p2wpkh(seed=b"w"):
    return b"\x00\x14" + hashlib.sha256(seed).digest()[:20]


def op_return(n):
    return b"\x6a" + push(b"D" * n)


def stamp_multisig():
    fake = b"\x02" + off_curve_x()
    return b"\x51" + push(REAL_KEY) + push(fake) + push(fake) + b"\x53\xae"


def real_multisig():
    return b"\x51" + push(REAL_KEY) + b"\x51\xae"


def inscription_witness(payload_len=500):
    """Taproot script-path spend carrying an ord-style envelope."""
    script = (push(REAL_KEY[1:]) + b"\xac" + b"\x00\x63" + push(b"ord")
              + push(b"\x01") + push(b"text/plain") + b"\x00")
    left = payload_len
    while left > 0:
        n = min(520, left)
        script += push(b"I" * n)
        left -= n
    script += b"\x68"
    control = b"\xc0" + REAL_KEY[1:]
    return [b"S" * 64, script, control]


def tx(inputs, outputs, witnesses=None, version=2, locktime=0):
    """inputs: [(prev_txid, vout, scriptsig)], outputs: [(value, spk)]."""
    body = write_varint(len(inputs))
    for prev, vout, ss in inputs:
        body += prev + struct.pack("<I", vout) + write_varint(len(ss)) + ss
        body += struct.pack("<I", 0xFFFFFFFF)
    body += write_varint(len(outputs))
    for value, spk in outputs:
        body += struct.pack("<Q", value) + write_varint(len(spk)) + spk
    ver = struct.pack("<I", version)
    lt = struct.pack("<I", locktime)
    legacy = ver + body + lt
    if witnesses is None:
        return legacy, dsha(legacy), dsha(legacy)
    wit = b""
    for stack in witnesses:
        wit += write_varint(len(stack))
        for item in stack:
            wit += write_varint(len(item)) + item
    full = ver + b"\x00\x01" + body + wit + lt
    return full, dsha(legacy), dsha(full)


def coinbase(height, extra_outputs=(), witness_root=None):
    ss = push(struct.pack("<I", height)) + os.urandom(4)
    outs = [(50_0000_0000, p2wpkh(b"miner"))] + list(extra_outputs)
    if witness_root is not None:
        reserved = b"\x00" * 32
        commit = dsha(witness_root + reserved)
        outs.append((0, b"\x6a\x24\xaa\x21\xa9\xed" + commit))
        return tx([(b"\x00" * 32, 0xFFFFFFFF, ss)], outs, witnesses=[[reserved]])
    return tx([(b"\x00" * 32, 0xFFFFFFFF, ss)], outs)


def block(txs_no_coinbase, height, prev=b"\x00" * 32, coinbase_extra=(), timestamp=1700000000):
    """txs_no_coinbase: list of (raw, txid, wtxid). Returns (raw_block, block_hash)."""
    has_wit = any(r[1] != r[2] for r in txs_no_coinbase)
    wroot = None
    if has_wit:
        wroot = merkle_root([b"\x00" * 32] + [t[2] for t in txs_no_coinbase])
    cb = coinbase(height, coinbase_extra, wroot)
    txs = [cb] + list(txs_no_coinbase)
    root = merkle_root([t[1] for t in txs])
    header = (struct.pack("<I", 0x20000000) + prev + root
              + struct.pack("<III", timestamp, 0x207fffff, 0))
    raw = header + write_varint(len(txs)) + b"".join(t[0] for t in txs)
    return raw, dsha(header)


def fake_prev(i):
    return hashlib.sha256(b"prev%d" % i).digest()


def mixed_block(height=800000, prev=b"\x00" * 32, seed=0):
    """A block exercising every carrier."""
    txs = [
        # clean payment
        tx([(fake_prev(seed * 10 + 1), 0, b"")], [(100000, p2wpkh(b"a"))],
           witnesses=[[b"s" * 71, REAL_KEY]]),
        # inscription reveal + postage (P2TR dust)
        tx([(fake_prev(seed * 10 + 2), 0, b"")], [(546, p2tr(b"post%d" % seed))],
           witnesses=[inscription_witness(1200)]),
        # oversized OP_RETURN + change
        tx([(fake_prev(seed * 10 + 3), 1, b"\x00" * 10)],
           [(0, op_return(120)), (5000, p2wpkh(b"c"))]),
        # stamp multisig only -> stripped
        tx([(fake_prev(seed * 10 + 4), 0, b"\x00" * 10)], [(7800, stamp_multisig())]),
        # oversized scriptSig
        tx([(fake_prev(seed * 10 + 5), 0, b"\x01" * 1700)], [(9000, p2wpkh(b"d"))]),
        # small OP_RETURN (monetary) and real multisig
        tx([(fake_prev(seed * 10 + 6), 0, b"")], [(0, op_return(40)), (2000, real_multisig())]),
    ]
    return block(txs, height, prev)
