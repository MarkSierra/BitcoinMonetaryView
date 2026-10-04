# SPDX-License-Identifier: AGPL-3.0-or-later
"""
HTTP(S) transport to the node, optionally through a SOCKS5 proxy (Tor).

Standard library only. TLS certificates are always verified; a custom CA file
(e.g. Start9's root CA) can be added, verification can never be disabled.
"""

import http.client
import ipaddress
import socket
import ssl
import struct
import threading
import urllib.parse

MAX_RESPONSE_BYTES = 64 * 1024 * 1024   # a 4 MB block is ~8 MB as hex


class NodeError(Exception):
    """Base class for node communication problems."""


class NodeUnreachable(NodeError):
    pass


class NodeAuthError(NodeError):
    pass


def socks5_connect(proxy_host, proxy_port, dest_host, dest_port, timeout):
    """
    Open a TCP connection to dest through a SOCKS5 proxy.

    The destination hostname is sent to the proxy unresolved (ATYP=domain), so
    no local DNS lookup happens -- required for .onion and avoids DNS leaks.
    """
    s = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        s.sendall(b"\x05\x01\x00")                       # no authentication
        if _recv_exact(s, 2) != b"\x05\x00":
            raise NodeUnreachable("SOCKS5 proxy refused the handshake")
        host = dest_host.encode("idna")
        if len(host) > 255:
            raise NodeUnreachable("hostname too long for SOCKS5")
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(host)]) + host
                  + struct.pack(">H", dest_port))
        rep = _recv_exact(s, 4)
        if rep[1] != 0:
            raise NodeUnreachable(f"SOCKS5 proxy could not connect (code {rep[1]})")
        atyp = rep[3]
        if atyp == 1:
            _recv_exact(s, 4 + 2)
        elif atyp == 4:
            _recv_exact(s, 16 + 2)
        elif atyp == 3:
            _recv_exact(s, _recv_exact(s, 1)[0] + 2)
        else:
            raise NodeUnreachable("SOCKS5 proxy sent an invalid reply")
        return s
    except BaseException:
        s.close()
        raise


def _recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise NodeUnreachable("connection closed by proxy")
        buf += chunk
    return buf


class _Conn(http.client.HTTPConnection):
    def __init__(self, host, port, timeout, proxy, tls_ctx):
        super().__init__(host, port, timeout=timeout)
        self._proxy = proxy
        self._tls_ctx = tls_ctx

    def connect(self):
        if self._proxy:
            self.sock = socks5_connect(self._proxy[0], self._proxy[1],
                                       self.host, self.port, self.timeout)
        else:
            self.sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if self._tls_ctx is not None:
            self.sock = self._tls_ctx.wrap_socket(self.sock, server_hostname=self.host)


def is_local_host(host):
    """True for loopback and for StartOS-internal service hostnames."""
    h = (host or "").lower().strip("[]")
    if h in ("localhost",) or h.endswith(".startos") or h.endswith(".embassy"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


class Transport:
    """Thread-safe: one keep-alive connection per thread."""

    def __init__(self, url, timeout=60.0, proxy=None, cafile=None):
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("http", "https"):
            raise ValueError("node URL must start with http:// or https://")
        if not u.hostname:
            raise ValueError("node URL has no host")
        if u.username or u.password:
            raise ValueError("put RPC credentials in the user/password settings, not in the URL")
        self.scheme = u.scheme
        self.host = u.hostname
        self.port = u.port or (443 if u.scheme == "https" else 8332)
        self.base_path = u.path.rstrip("/")
        self.timeout = timeout
        self.proxy = proxy
        self.tls_ctx = None
        if u.scheme == "https":
            ctx = ssl.create_default_context()
            if cafile:
                ctx.load_verify_locations(cafile=cafile)
            ctx.check_hostname = True
            ctx.verify_mode = ssl.CERT_REQUIRED
            self.tls_ctx = ctx
        self._local = threading.local()

    @property
    def plaintext_remote(self):
        return self.scheme == "http" and not is_local_host(self.host) and not self.host.endswith(".onion")

    def describe(self):
        via = f" via SOCKS5 {self.proxy[0]}:{self.proxy[1]}" if self.proxy else ""
        return f"{self.scheme}://{self.host}:{self.port}{self.base_path}{via}"

    def _conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = _Conn(self.host, self.port, self.timeout, self.proxy, self.tls_ctx)
            self._local.conn = c
        return c

    def close(self):
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

    def request(self, method, path, body=None, headers=None):
        """Returns (status, body_bytes). Retries once on a stale keep-alive connection."""
        hdrs = {"Host": f"{self.host}:{self.port}", "User-Agent": "BitcoinMonetaryView"}
        if headers:
            hdrs.update(headers)
        for attempt in (0, 1):
            conn = self._conn()
            try:
                conn.request(method, self.base_path + path, body=body, headers=hdrs)
                resp = conn.getresponse()
                length = resp.getheader("Content-Length")
                if length is not None and int(length) > MAX_RESPONSE_BYTES:
                    self.close()
                    raise NodeError("response from node is too large")
                data = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(data) > MAX_RESPONSE_BYTES:
                    self.close()
                    raise NodeError("response from node is too large")
                if resp.getheader("Connection", "").lower() == "close":
                    self.close()
                return resp.status, data
            except ssl.SSLCertVerificationError as e:
                self.close()
                raise NodeUnreachable(
                    f"TLS certificate not trusted ({e.verify_message}). If your node uses its own "
                    "certificate authority (e.g. Start9), set rpc_cafile to its root CA file.") from None
            except (http.client.RemoteDisconnected, http.client.BadStatusLine,
                    BrokenPipeError, ConnectionResetError) as e:
                self.close()
                if attempt == 0:
                    continue
                raise NodeUnreachable(f"connection to node lost: {e}") from None
            except (OSError, http.client.HTTPException) as e:
                self.close()
                raise NodeUnreachable(f"cannot reach node at {self.describe()}: {e}") from None
        raise NodeUnreachable("cannot reach node")
