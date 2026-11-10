# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Settings: one schema, three sources.

Precedence: command line > environment (BMV_<NAME>) > <data-dir>/settings.json
> defaults. A bad settings file never crashes the app: unknown keys are ignored and
a missing value is its default. A file that cannot be read, or an invalid value,
keeps what the file said before, with a warning. When there is no earlier value
(the app has just started), the default applies and the setting is listed in
`Config.unknown`: the web server and the scanner refuse to act on a web login or
on spam rules they do not know.
"""

import argparse
import json
import logging
import os
import re
import tempfile
import threading

log = logging.getLogger("bmv.config")

SETTINGS_FILE = "settings.json"
_TIME_WINDOW = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")


def _bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "on"):
        return True
    if isinstance(v, str) and v.strip().lower() in ("0", "false", "no", "off", ""):
        return False
    raise ValueError("expected true/false")


def _int(lo, hi):
    def conv(v):
        if isinstance(v, bool):
            raise ValueError("expected a number")
        n = int(v)
        if not lo <= n <= hi:
            raise ValueError(f"must be between {lo} and {hi}")
        return n
    return conv


def _opt_int(lo, hi):
    inner = _int(lo, hi)

    def conv(v):
        if v is None or v == "" or v == "auto":
            return None
        return inner(v)
    return conv


def _str(v):
    if v is None:
        return ""
    if not isinstance(v, (str, int, float)):
        raise ValueError("expected text")
    return str(v).strip()


def _choice(*opts):
    def conv(v):
        v = _str(v).lower()
        if v not in opts:
            raise ValueError(f"must be one of {', '.join(opts)}")
        return v
    return conv


def _url(v):
    v = _str(v)
    if not re.match(r"^https?://[^\s/]+(:\d+)?(/\S*)?$", v):
        raise ValueError("must be an http:// or https:// URL")
    if "@" in v.split("/")[2]:
        raise ValueError("must not contain credentials; set rpc_user/rpc_password or rpc_cookie_file instead")
    return v


def _hostport(v):
    v = _str(v)
    if v == "":
        return ""
    m = re.match(r"^([A-Za-z0-9.\-]+|\[[0-9a-fA-F:]+\]):(\d{1,5})$", v)
    if not m or not 0 < int(m.group(2)) < 65536:
        raise ValueError("must be host:port, e.g. 127.0.0.1:9050")
    return v


def _window(v):
    v = _str(v)
    if v and not _TIME_WINDOW.match(v):
        raise ValueError("must look like 01:00-07:00 (24h) or be empty")
    return v


def _tz(v):
    v = _str(v) or "UTC"
    from zoneinfo import ZoneInfo
    try:
        ZoneInfo(v)
    except Exception:           # ZoneInfoNotFoundError is a KeyError; bad paths raise ValueError or OSError
        raise ValueError(f"unknown time zone {v[:64]!r}") from None
    return v


def _hosts(v):
    if isinstance(v, list):
        items = v
    else:
        items = [x for x in _str(v).split(",")]
    out = [x.strip().lower() for x in items if str(x).strip()]
    for h in out:
        if not re.match(r"^[a-z0-9.\-*:\[\]]+$", h):
            raise ValueError(f"invalid host name {h!r}")
    return out


class Setting:
    def __init__(self, name, conv, default, help, secret=False, ui_editable=False, kind="string"):
        self.name, self.conv, self.default, self.help = name, conv, default, help
        self.secret, self.ui_editable, self.kind = secret, ui_editable, kind


SCHEMA = [
    # --- node connection
    Setting("rpc_url", _url, "http://127.0.0.1:8332", "Bitcoin Core/Knots/Roots RPC URL"),
    Setting("rpc_user", _str, "", "RPC username (leave empty when using a cookie file)"),
    Setting("rpc_password", _str, "", "RPC password", secret=True),
    Setting("rpc_cookie_file", _str, "", "Path to bitcoind's .cookie file (read-only)"),
    Setting("rpc_cafile", _str, "", "Extra CA certificate (PEM) to trust for https RPC, e.g. Start9 root CA"),
    Setting("tor_proxy", _hostport, "", "SOCKS5 proxy for .onion RPC, e.g. 127.0.0.1:9050"),
    Setting("use_rest", _bool, True, "Use bitcoind's REST interface if it is already enabled (faster)",
            kind="boolean"),
    Setting("rpc_timeout", _int(5, 900), 120, "RPC timeout in seconds", kind="integer"),
    # --- scanning
    Setting("speed_profile", _choice("eco", "balanced", "full"), "eco",
            "eco = gentlest on the node, full = fastest", ui_editable=True, kind="string"),
    Setting("scan_window", _window, "", "Only scan history during this daily time window, e.g. 01:00-07:00",
            ui_editable=True),
    Setting("timezone", _tz, "UTC", "Time zone for scan_window (IANA name, e.g. Europe/Berlin)",
            ui_editable=True),
    Setting("quick_pass_blocks", _int(0, 50000), 1000, "Recent blocks analysed first for a quick result",
            kind="integer"),
    Setting("sample_every", _int(0, 100000), 100,
            "Before the full scan, analyse every Nth block of the history for an early whole-chain estimate "
            "(0 = off)", kind="integer"),
    Setting("carrier_policy", _bool, True,
            "Keep oversized OP_RETURNs that are verified payment-protocol envelopes (upstream carrier policy)",
            kind="boolean"),
    Setting("dust_start_height", _opt_int(0, 10_000_000), None,
            "Height from which P2TR dust counts (auto: 767430 on mainnet, 0 on test networks)",
            kind="integer"),
    Setting("bloom_mb", _int(8, 4096), 256, "Memory for the spent-output filter (MB)", kind="integer"),
    # --- web interface
    Setting("bind", _str, "127.0.0.1", "Address the web interface listens on"),
    Setting("port", _int(1, 65535), 8338, "Port of the web interface", kind="integer"),
    Setting("auth_user", _str, "", "Username for the web interface (optional basic auth)"),
    Setting("auth_password", _str, "", "Password for the web interface", secret=True),
    Setting("tls_cert", _str, "", "TLS certificate (PEM) to serve the web interface over https"),
    Setting("tls_key", _str, "", "TLS private key (PEM)"),
    Setting("allowed_hosts", _hosts, [], "Extra Host names allowed for the web interface (comma separated)",
            kind="array"),
    Setting("log_level", _choice("debug", "info", "warning", "error"), "info", "Log verbosity"),
]
BY_NAME = {s.name: s for s in SCHEMA}


def default_data_dir():
    return os.environ.get("BMV_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".bitcoinmonetaryview")


OWN_SETTINGS_PLATFORMS = ("umbrel",)
PROXY_LOGIN_PLATFORMS = ("umbrel",)     # their proxy does the web login; the app's own is not used there


class Config:
    """Resolved settings with per-key source tracking and live reload of the file."""

    def __init__(self, data_dir, cli=None, env=None):
        self.data_dir = os.path.abspath(data_dir)
        self.cli = {k: v for k, v in (cli or {}).items() if v is not None and k in BY_NAME}
        self.env = env if env is not None else os.environ
        self.managed_by = _str(self.env.get("BMV_MANAGED_BY", "")).lower()
        self.values = {}
        self.sources = {}
        self.warnings = []
        self.unknown = set()         # settings the file should give but cannot, with no earlier value to keep
        self._file_mtime = None
        self._file_values = None     # valid values of the last readable file (None: none read yet)
        self._file_unknown = set()
        self._lock = threading.Lock()
        self.load()

    @property
    def path(self):
        return os.path.join(self.data_dir, SETTINGS_FILE)

    @property
    def platform(self):
        """True when a platform (StartOS, Umbrel, ...) runs the app: its proxy fronts the web UI
        (and handles login) and it connects the app to the node over its internal network."""
        return bool(self.managed_by)

    @property
    def managed(self):
        """True when the platform owns the settings (StartOS Actions), so they are read-only here.
        Umbrel has no settings UI for apps, so there the app's own settings stay editable."""
        return self.platform and self.managed_by not in OWN_SETTINGS_PLATFORMS

    @property
    def auth_unknown(self):
        """True while a damaged settings file leaves open whether a web login is set."""
        if self.managed_by in PROXY_LOGIN_PLATFORMS:
            return False
        return bool(self.unknown & {"auth_user", "auth_password"})

    @property
    def auth_incomplete(self):
        """True when a web login user is set without a password: no sign-in can be checked."""
        return bool(self.values["auth_user"]) and not self.values["auth_password"]

    def _read_file(self):
        """(settings, None), or (None, reason) when the file exists but cannot be read."""
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            self._file_mtime = None
            return {}, None
        self._file_mtime = st.st_mtime_ns
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("top level must be an object")
            return data, None
        except (ValueError, OSError) as e:
            return None, str(e)

    def _file_settings(self, warnings):
        """Valid values from the settings file and the names it leaves unknown. What cannot be read
        keeps its earlier value; without one it is unknown."""
        fdata, error = self._read_file()
        if fdata is None:
            if self._file_values is None:
                warnings.append(f"{SETTINGS_FILE} could not be read ({error}); using defaults")
                return {}, {s.name for s in SCHEMA}
            warnings.append(f"{SETTINGS_FILE} could not be read ({error}); keeping the previous settings")
            return self._file_values, self._file_unknown
        previous, good, unknown = self._file_values or {}, {}, set()
        for s in SCHEMA:
            if s.name not in fdata:
                continue
            try:
                good[s.name] = s.conv(fdata[s.name])
            except Exception as e:      # ValueError, TypeError and whatever zoneinfo raises
                if s.name in previous:
                    good[s.name] = previous[s.name]
                    warnings.append(f"setting {s.name}: invalid value in {SETTINGS_FILE} ({e}); "
                                    f"keeping the previous value")
                else:
                    unknown.add(s.name)
                    warnings.append(f"setting {s.name}: invalid value in {SETTINGS_FILE} ({e}); "
                                    f"using default")
        self._file_values, self._file_unknown = good, unknown
        return good, unknown

    def load(self):
        with self._lock:
            warnings = []
            from_file, unknown = self._file_settings(warnings)
            values, sources = {}, {}
            for s in SCHEMA:
                val, src = s.default, "default"
                if s.name in from_file:
                    val, src = from_file[s.name], "file"
                ev = self.env.get("BMV_" + s.name.upper())
                if ev is not None:
                    try:
                        val, src = s.conv(ev), "env"
                    except Exception as e:
                        warnings.append(f"setting {s.name}: invalid environment value ({e}); ignored")
                if s.name in self.cli:
                    val, src = s.conv(self.cli[s.name]), "cli"   # CLI errors are user errors: raise
                values[s.name], sources[s.name] = val, src
            self.values, self.sources, self.warnings = values, sources, warnings
            self.unknown = {n for n in unknown if sources[n] == "default"}   # environment and CLI still decide
            if values["auth_user"] and not values["auth_password"]:
                warnings.append("auth_user is set without auth_password: the web interface stays locked "
                                "until both are set")
            for w in warnings:
                log.warning(w)

    def file_changed(self):
        try:
            m = os.stat(self.path).st_mtime_ns
        except FileNotFoundError:
            m = None
        return m != self._file_mtime

    def get(self, name):
        return self.values[name]

    def __getattr__(self, name):
        if name in BY_NAME:
            return self.values[name]
        raise AttributeError(name)

    def can_edit(self, name):
        """UI may change a setting only if it is UI-editable, not managed, and not pinned by env/CLI."""
        s = BY_NAME.get(name)
        return bool(s and s.ui_editable and not self.managed and self.sources.get(name) in ("default", "file"))

    def update_file(self, changes):
        """Validate and merge changes into settings.json (atomic write, mode 0600)."""
        clean = {}
        for k, v in changes.items():
            if not self.can_edit(k):
                raise PermissionError(f"setting {k} cannot be changed here")
            clean[k] = BY_NAME[k].conv(v)
        with self._lock:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if not isinstance(existing, dict):
                    raise ValueError
            except FileNotFoundError:
                existing = {}
            except ValueError:          # rewriting it would drop the settings it still holds, the login too
                raise PermissionError(f"{SETTINGS_FILE} is damaged; repair or delete it before "
                                      "changing settings here") from None
            existing.update(clean)
            write_private_json(self.path, existing)
        self.load()

    def public_view(self):
        """Settings for the UI/API: secrets never leave the process."""
        out = []
        for s in SCHEMA:
            v = self.values[s.name]
            if s.secret:
                v = "********" if v else ""
            out.append({"name": s.name, "value": v, "source": self.sources[s.name], "editable": self.can_edit(s.name)})
        return out


def write_private_json(path, data):
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".settings.", dir=d)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def build_arg_parser():
    ap = argparse.ArgumentParser(
        prog="bitcoinmonetaryview",
        description="Read-only spam analysis for your Bitcoin Core/Knots/Roots node, "
                    "based on the Monetary Node rules.")
    ap.add_argument("--data-dir", default=None, help="where the app keeps its own data (default ~/.bitcoinmonetaryview)")
    for s in SCHEMA:
        flag = "--" + s.name.replace("_", "-")
        if s.kind == "boolean":
            ap.add_argument(flag, dest=s.name, default=None, type=_bool, metavar="true|false", help=s.help)
        else:
            ap.add_argument(flag, dest=s.name, default=None, help=s.help)
    ap.add_argument("--check", action="store_true", help="run the connection test and exit")
    ap.add_argument("--version", action="store_true", help="print version and exit")
    return ap
