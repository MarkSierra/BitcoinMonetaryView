# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Apply the Monetary Node rules to one block.

`analyze_block()` mirrors upstream `monetary_store.strip_block()` step by step
and produces the same monetary-store record (so "stored bytes" is the exact
size a Monetary Node would write). In the same parse it collects what this app
needs on top: txids/wtxids for integrity checks, the outpoints every input
spends, and the spam outputs that enter the UTXO set.

Two deliberate, documented extensions, both parameters, neither changing the
upstream rule logic:

* ``dust_start_height`` -- upstream hard-codes the P2TR dust era to start at
  mainnet height 767,430. Test networks pass their own start height.
* ``carrier_policy`` -- upstream's ``carrier_policy.Policy`` (documented in its
  CARRIER_POLICY.md, not yet wired into monetary_store.py). When given, an
  oversized OP_RETURN that the policy positively identifies as a known payment
  protocol envelope is retained. The size test itself is unchanged, so with
  ``carrier_policy=None`` the result is byte-identical to upstream.

tests/test_rules_parity.py asserts that parity against the vendored upstream.
"""

import struct

from .upstream import monetary_store as up

OP_RETURN = up.OP_RETURN
DEFAULT_OP_RETURN_LIMIT = 83          # upstream --op-return-limit default
DEFAULT_SCRIPTSIG_LIMIT = 1650        # upstream --scriptsig-limit default
MAINNET_DUST_START = up.INSCRIPTION_START
DUST_THRESHOLD = up.DUST_THRESHOLD
MAX_SCRIPT_SIZE = 10000               # consensus: larger scripts never enter the UTXO set

WITNESS_COMMITMENT_PREFIX = b"\x6a\x24\xaa\x21\xa9\xed"

# Carrier keys used throughout the app (bytes removed per carrier).
CARRIERS = ("envelope", "op_return", "multisig", "scriptsig")

# UTXO kinds for spam that enters the UTXO set.
UTXO_KIND_DATA_KEY = 1     # bare multisig / P2PK with keys not on secp256k1
UTXO_KIND_P2TR_DUST = 2    # inscription-era P2TR dust (retained in blocks)
UTXO_KIND_NAMES = {UTXO_KIND_DATA_KEY: "data_key", UTXO_KIND_P2TR_DUST: "p2tr_dust"}


class BlockInvalid(ValueError):
    """The block bytes do not match their header or are malformed."""


def classify_output(script, value, height, op_return_limit,
                    dust_start_height=MAINNET_DUST_START, carrier_policy=None):
    """
    Upstream ``classify_output`` with the two parameters described above.

    Returns (kind, reason, protocol). kind is 'spam', 'dust' or 'monetary'.
    """
    if script and script[0] == OP_RETURN:
        if len(script) > op_return_limit:
            if carrier_policy is not None:
                verdict = carrier_policy.should_retain(script)
                if verdict.retain and verdict.protocol:
                    return "monetary", "retained_protocol", verdict.protocol
            return "spam", "op_return", None
        return "monetary", "", None
    if up.is_data_multisig(script):
        return "spam", "multisig", None
    if up.is_data_pubkey(script):
        return "spam", "multisig", None
    if (value < DUST_THRESHOLD and height >= dust_start_height
            and len(script) == 34 and script[0] == 0x51 and script[1] == 0x20):
        return "dust", "p2tr_dust", None
    return "monetary", "", None


def enters_utxo_set(script):
    """Core's CScript::IsUnspendable() complement."""
    return not ((script and script[0] == OP_RETURN) or len(script) > MAX_SCRIPT_SIZE)


def analyze_block(raw, height, op_return_limit=DEFAULT_OP_RETURN_LIMIT,
                  scriptsig_limit=DEFAULT_SCRIPTSIG_LIMIT,
                  dust_start_height=MAINNET_DUST_START, carrier_policy=None,
                  build_record=False):
    """
    Analyse one serialized block.

    Returns a dict:
        original, stored           block bytes vs monetary-store record bytes
        envelope/op_return/multisig/scriptsig   bytes removed per carrier
        whole/modified/stripped    transaction treatment counts
        dust_outputs               P2TR dust outputs (kept in blocks)
        filter_entries             dropped outputs that keep a filter entry
        retained_protocol          oversized OP_RETURNs kept by the carrier policy
        tx_count, weight, header, txids, wtxids, has_witness
        coinbase_witness           witness stack of the coinbase input
        coinbase_outputs           scriptPubKeys of the coinbase
        spends                     [(prev_txid, vout)] for every non-coinbase input
        utxo_spam                  [(txid, vout, value, kind, script_len)]
        record                     the store record (only if build_record)

    Raises BlockInvalid on malformed data.
    """
    try:
        return _analyze(raw, height, op_return_limit, scriptsig_limit,
                        dust_start_height, carrier_policy, build_record)
    except (ValueError, IndexError, struct.error) as e:
        if isinstance(e, BlockInvalid):
            raise
        raise BlockInvalid(f"malformed block at height {height}: {e}") from None


_U32 = struct.Struct("<I").unpack_from
_U64 = struct.Struct("<Q").unpack_from
_U16 = struct.Struct("<H").unpack_from


def _rv(d, p, n):
    """CompactSize at d[p] -> (value, new_p). Bounds-checked."""
    if p >= n:
        raise BlockInvalid("truncated")
    b = d[p]
    if b < 0xFD:
        return b, p + 1
    if b == 0xFD:
        if p + 3 > n:
            raise BlockInvalid("truncated")
        return _U16(d, p + 1)[0], p + 3
    if b == 0xFE:
        if p + 5 > n:
            raise BlockInvalid("truncated")
        return _U32(d, p + 1)[0], p + 5
    if p + 9 > n:
        raise BlockInvalid("truncated")
    return _U64(d, p + 1)[0], p + 9


def _envelope_payload(script):
    """upstream envelope_payload with an exact fast path: an envelope needs an
    OP_IF (0x63) or OP_NOTIF (0x64) byte, so scripts without either yield 0."""
    if b"\x63" not in script and b"\x64" not in script:
        return 0
    return up.envelope_payload(script)


def _analyze(raw, height, op_return_limit, scriptsig_limit,
             dust_start_height, carrier_policy, build_record):
    # Hand-inlined parser (same steps as upstream strip_block, parity-tested):
    # local variables and slices instead of Cursor method calls, ~2x faster.
    dsha = up.dsha
    write_varint = up.write_varint
    is_tr_path = up.is_taproot_script_path
    d = raw
    n = len(d)
    if n < 81:
        raise BlockInvalid("block shorter than a header")
    header = d[:80]
    n_tx, p = _rv(d, 80, n)
    if n_tx == 0 or n_tx > n:
        raise BlockInvalid("implausible transaction count")

    tx_parts = [] if build_record else None
    filter_entries = []
    txids, wtxids = [], []
    spends, utxo_spam = [], []
    coinbase_witness, coinbase_outputs = [], []
    st = {"whole": 0, "modified": 0, "stripped": 0, "dust_outputs": 0,
          "envelope": 0, "op_return": 0, "multisig": 0, "scriptsig": 0,
          "retained_protocol": 0}
    stored_tx_bytes = 0
    base_size = 80 + len(write_varint(n_tx))
    has_witness = False

    for tx_index in range(n_tx):
        tx_start = p
        p += 4
        segwit = d[p:p + 2] == b"\x00\x01"
        if segwit:
            p += 2
            has_witness = True

        io_start = p
        n_in, p = _rv(d, p, n)
        if n_in > n:
            raise BlockInvalid("implausible input count")
        scriptsig_spam = 0
        tx_spends = []
        for _ in range(n_in):
            if p + 36 > n:
                raise BlockInvalid("truncated")
            prev = d[p:p + 32]
            vout = _U32(d, p + 32)[0]
            sl, p = _rv(d, p + 36, n)
            p += sl + 4
            if p > n:
                raise BlockInvalid("truncated")
            if sl > scriptsig_limit:
                scriptsig_spam += sl
            tx_spends.append((prev, vout))

        n_out, p = _rv(d, p, n)
        if n_out > n:
            raise BlockInvalid("implausible output count")
        out_monetary = 0
        dropped_outs = []
        tx_utxo = []
        for vout in range(n_out):
            if p + 8 > n:
                raise BlockInvalid("truncated")
            amount = _U64(d, p)[0]
            sl, p = _rv(d, p + 8, n)
            if p + sl > n:
                raise BlockInvalid("truncated")
            spk = d[p:p + sl]
            p += sl
            if tx_index == 0:
                coinbase_outputs.append(spk)
            kind, reason, _proto = classify_output(
                spk, amount, height, op_return_limit, dust_start_height,
                carrier_policy)
            if kind == "monetary":
                if reason == "retained_protocol":
                    st["retained_protocol"] += 1
                out_monetary += 1
            elif kind == "spam":
                dropped_outs.append((vout, amount, spk))
                st[reason] = st.get(reason, 0) + sl
                if enters_utxo_set(spk):
                    tx_utxo.append((vout, amount, UTXO_KIND_DATA_KEY, sl))
            else:  # dust
                st["dust_outputs"] += 1
                out_monetary += 1
                tx_utxo.append((vout, amount, UTXO_KIND_P2TR_DUST, sl))
        io_end = p

        envelope_bytes = 0
        if segwit:
            for i in range(n_in):
                count, p = _rv(d, p, n)
                if count > n:
                    raise BlockInvalid("implausible witness item count")
                items = []
                for _ in range(count):
                    il, p = _rv(d, p, n)
                    if p + il > n:
                        raise BlockInvalid("truncated")
                    items.append(d[p:p + il])
                    p += il
                if tx_index == 0 and i == 0:
                    coinbase_witness = items
                if is_tr_path(items):
                    envelope_bytes += _envelope_payload(items[-2])
                elif len(items) >= 2 and items[-1]:
                    envelope_bytes += _envelope_payload(items[-1])
        p += 4
        if p > n:
            raise BlockInvalid("truncated")
        tx_end = p

        if segwit:
            legacy = (raw[tx_start:tx_start + 4] + raw[io_start:io_end]
                      + raw[tx_end - 4:tx_end])
            wtxid = dsha(raw[tx_start:tx_end])
        else:
            legacy = raw[tx_start:tx_end]
            wtxid = None
        txid = dsha(legacy)
        txids.append(txid)
        wtxids.append(txid if wtxid is None else wtxid)
        base_size += len(legacy)

        if tx_index > 0:
            spends.extend(tx_spends)
        for vout, amount, ukind, slen in tx_utxo:
            utxo_spam.append((txid, vout, amount, ukind, slen))

        st["envelope"] += envelope_bytes
        st["scriptsig"] += scriptsig_spam
        for vout, amount, spk in dropped_outs:
            filter_entries.append((txid, vout, amount, height, spk))

        touched = bool(envelope_bytes or dropped_outs or scriptsig_spam)
        if touched and out_monetary == 0:
            st["stripped"] += 1
            part_len = 1 + 32
            if build_record:
                tx_parts.append(bytes([up.FLAG_STRIPPED]) + txid)
        elif touched:
            st["modified"] += 1
            body = up.rebuild_without(raw, tx_start, io_start, io_end, tx_end,
                                      dropped_outs)
            part_len = 1 + 32 + len(write_varint(len(body))) + len(body)
            if build_record:
                tx_parts.append(bytes([up.FLAG_MODIFIED]) + txid
                                + write_varint(len(body)) + body)
        else:
            st["whole"] += 1
            blen = tx_end - tx_start
            part_len = 1 + len(write_varint(blen)) + blen
            if build_record:
                tx_parts.append(bytes([up.FLAG_WHOLE]) + write_varint(blen)
                                + raw[tx_start:tx_end])
        stored_tx_bytes += part_len

    if p != n:
        raise BlockInvalid("trailing bytes after last transaction")

    filter_bytes = [txid + write_varint(vout) + struct.pack("<Q", amount)
                    + struct.pack("<I", h) + write_varint(len(spk)) + spk
                    for txid, vout, amount, h, spk in filter_entries]
    # record = magic(4) version(2) length(4) digest(32) || body
    # body   = blockhash(32) header(80) varint(n_tx) tx_parts varint(n_filter) filters
    body_len = (32 + 80 + len(write_varint(n_tx)) + stored_tx_bytes
                + len(write_varint(len(filter_entries)))
                + sum(len(f) for f in filter_bytes))
    stored = 4 + 2 + 4 + 32 + body_len

    out = dict(st)
    out.update({
        "original": len(raw),
        "stored": stored,
        "filter_entries": len(filter_entries),
        "tx_count": n_tx,
        "weight": base_size * 3 + len(raw),
        "header": header,
        "txids": txids,
        "wtxids": wtxids,
        "has_witness": has_witness,
        "coinbase_witness": coinbase_witness,
        "coinbase_outputs": coinbase_outputs,
        "spends": spends,
        "utxo_spam": utxo_spam,
    })
    if build_record:
        payload = [header, write_varint(n_tx)] + tx_parts
        payload.append(write_varint(len(filter_entries)))
        payload.extend(filter_bytes)
        rbody = dsha(header) + b"".join(payload)
        record = (up.STORE_MAGIC + struct.pack("<H", up.STORE_VERSION)
                  + struct.pack("<I", len(rbody) + 32) + dsha(rbody) + rbody)
        assert len(record) == stored
        out["record"] = record
    return out


def verify_block(result, expected_hash=None, expected_prev=None):
    """
    Check that analysed block bytes are exactly the block the header commits to.

    * double-SHA256 of the header equals the requested hash
    * merkle root over the computed txids equals the header's merkle root
    * if any transaction carries witness data, the coinbase witness commitment
      (BIP141) equals the merkle root over wtxids -- without this, tampered
      witness bytes (where inscriptions live) would go unnoticed
    * optionally, the header's prev-hash links to the expected parent

    Raises BlockInvalid. Hashes are internal byte order (not hex display order).
    """
    header = result["header"]
    if expected_hash is not None and up.dsha(header) != expected_hash:
        raise BlockInvalid("block hash does not match the requested hash")
    if expected_prev is not None and header[4:36] != expected_prev:
        raise BlockInvalid("block does not connect to the expected parent")
    if up.merkle_root(result["txids"]) != header[36:68]:
        raise BlockInvalid("merkle root mismatch: transaction data was altered")

    commitment = None
    for spk in result["coinbase_outputs"]:
        if len(spk) >= 38 and spk[:6] == WITNESS_COMMITMENT_PREFIX:
            commitment = spk[6:38]        # BIP141: the last matching output wins
    if result["has_witness"]:
        if commitment is None:
            raise BlockInvalid("witness data present but no witness commitment")
        wit = result["coinbase_witness"]
        if len(wit) != 1 or len(wit[0]) != 32:
            raise BlockInvalid("invalid coinbase witness reserved value")
        wtxids = [b"\x00" * 32] + list(result["wtxids"][1:])
        if up.dsha(up.merkle_root(wtxids) + wit[0]) != commitment:
            raise BlockInvalid("witness commitment mismatch: witness data was altered")
    return True


def block_hash(header):
    return up.dsha(header[:80])


def hash_to_hex(h):
    return h[::-1].hex()


def hex_to_hash(s):
    b = bytes.fromhex(s)
    if len(b) != 32:
        raise ValueError("block hash must be 32 bytes")
    return b[::-1]


def header_time(header):
    return struct.unpack_from("<I", header, 68)[0]


def coin_bytes(script_len):
    """Serialized coin size, as upstream chainstate_filter.coin_bytes()."""
    return 36 + 4 + 8 + script_len


LEVELDB_OVERHEAD_FACTOR = 1.35   # upstream chainstate_filter.py
