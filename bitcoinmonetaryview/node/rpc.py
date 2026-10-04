# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Read-only JSON-RPC client for Bitcoin Core / Knots.

SAFETY: every call -- including each element of a batch -- is checked against
ALLOWED below *before* anything is sent. Only read-only methods with
validated parameters pass. There is no way to call anything else through this
module, so the app cannot change node state even if other code is buggy.
"""

import base64
import json
import os
import re

from .transport import NodeAuthError, NodeError, NodeUnreachable, Transport

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


class RPCForbidden(NodeError):
    """A call outside the read-only whitelist was attempted (never sent)."""


class RPCError(NodeError):
    def __init__(self, code, message, method=""):
        super().__init__(f"{method}: {message} (code {code})" if method else f"{message} (code {code})")
        self.code = code
        self.rpc_message = message


RPC_IN_WARMUP = -28


def _no_params(p):
    return len(p) == 0


def _hash_param(p):
    return isinstance(p, str) and bool(_HEX64.match(p))


def _height(p):
    return isinstance(p, int) and not isinstance(p, bool) and 0 <= p < 100_000_000


# method -> validator(params list) ; all of these are read-only in Core/Knots
ALLOWED = {
    "getblockchaininfo": _no_params,
    "getnetworkinfo": _no_params,
    "getbestblockhash": _no_params,
    "getblockcount": _no_params,
    "getblockhash": lambda p: len(p) == 1 and _height(p[0]),
    "getblockheader": lambda p: len(p) in (1, 2) and _hash_param(p[0])
    and (len(p) == 1 or isinstance(p[1], bool)),
    # verbosity 0 only: raw serialized block
    "getblock": lambda p: len(p) == 2 and _hash_param(p[0]) and p[1] == 0
    and not isinstance(p[1], bool),
    # read-only, but heavy; hash_type "none" only (no hashing work, no index use)
    "gettxoutsetinfo": lambda p: list(p) == ["none"],
}


def check_allowed(method, params):
    """Raise RPCForbidden unless (method, params) is on the read-only whitelist."""
    if not isinstance(method, str) or method not in ALLOWED:
        raise RPCForbidden(f"RPC method {method!r} is not allowed (read-only whitelist)")
    if not isinstance(params, (list, tuple)) or not ALLOWED[method](list(params)):
        raise RPCForbidden(f"RPC parameters for {method!r} are not allowed: {params!r}")


def read_cookie(path):
    """Read the RPC cookie file. Opened strictly read-only; never created or modified."""
    fd = os.open(path, os.O_RDONLY)
    try:
        data = os.read(fd, 4096)
    finally:
        os.close(fd)
    text = data.decode("utf-8", "strict").strip()
    if ":" not in text:
        raise NodeAuthError("cookie file has an unexpected format")
    return text


class RPCClient:
    def __init__(self, url, user=None, password=None, cookie_file=None,
                 timeout=120.0, proxy=None, cafile=None):
        self.transport = Transport(url, timeout=timeout, proxy=proxy, cafile=cafile)
        self._user = user
        self._password = password
        self._cookie_file = cookie_file
        self._auth_header = None
        self._id = 0
        if not cookie_file and not (user and password is not None):
            raise ValueError("RPC credentials missing: set rpc_cookie_file or rpc_user/rpc_password")

    def __repr__(self):
        return f"<RPCClient {self.transport.describe()} auth={'cookie' if self._cookie_file else 'user/password'}>"

    def _auth(self, reload=False):
        if self._auth_header is None or reload:
            if self._cookie_file:
                try:
                    cred = read_cookie(self._cookie_file)
                except OSError as e:
                    raise NodeAuthError(f"cannot read RPC cookie file: {e.strerror}") from None
            else:
                cred = f"{self._user}:{self._password}"
            self._auth_header = "Basic " + base64.b64encode(cred.encode()).decode()
        return self._auth_header

    def _post(self, payload):
        body = json.dumps(payload).encode()
        for attempt in (0, 1):
            status, data = self.transport.request(
                "POST", "/", body=body,
                headers={"Authorization": self._auth(reload=attempt == 1),
                         "Content-Type": "application/json"})
            if status == 401 or status == 403:
                if attempt == 0 and self._cookie_file:
                    continue            # node restarted -> new cookie
                raise NodeAuthError("RPC authentication failed: check rpc_user/rpc_password or the cookie file"
                                    if status == 401 else
                                    "RPC access denied (403): the node does not allow this host "
                                    "(check rpcallowip/rpcbind)")
            break
        try:
            return json.loads(data)
        except ValueError:
            raise NodeError(f"node returned invalid JSON (HTTP {status})") from None

    def call(self, method, *params):
        check_allowed(method, params)
        self._id += 1
        resp = self._post({"jsonrpc": "1.0", "id": self._id, "method": method, "params": list(params)})
        if not isinstance(resp, dict):
            raise NodeError("unexpected RPC response")
        err = resp.get("error")
        if err:
            raise RPCError(err.get("code"), err.get("message", ""), method)
        return resp.get("result")

    def batch(self, calls):
        """calls: [(method, [params])]. Returns results in order; raises on any error."""
        if not calls:
            return []
        for m, p in calls:
            check_allowed(m, p)
        payload = []
        for m, p in calls:
            self._id += 1
            payload.append({"jsonrpc": "1.0", "id": self._id, "method": m, "params": list(p)})
        resp = self._post(payload)
        if not isinstance(resp, list) or len(resp) != len(calls):
            raise NodeError("unexpected batch RPC response")
        by_id = {r.get("id"): r for r in resp if isinstance(r, dict)}
        out = []
        for req in payload:
            r = by_id.get(req["id"])
            if r is None:
                raise NodeError("batch RPC response incomplete")
            if r.get("error"):
                raise RPCError(r["error"].get("code"), r["error"].get("message", ""), req["method"])
            out.append(r.get("result"))
        return out

    def close(self):
        self.transport.close()


__all__ = ["RPCClient", "RPCError", "RPCForbidden", "NodeError", "NodeAuthError",
           "NodeUnreachable", "check_allowed", "ALLOWED", "RPC_IN_WARMUP"]
