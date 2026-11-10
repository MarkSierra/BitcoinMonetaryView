# Settings reference

Every setting can be given in three ways (highest precedence first):

1. command line: `--rpc-url http://…` (underscores become dashes)
2. environment: `BMV_RPC_URL=http://…`
3. `settings.json` in the data directory: `{"rpc_url": "http://…"}`

A bad `settings.json` never crashes the app. Unknown keys are ignored and a missing value is its default.
Changes to the file are picked up while the app runs. This is the file a platform package (e.g. a StartOS
action) writes.

If the file cannot be read (damaged, cut short, a typo), or one value in it is invalid, the app keeps what
the file said before and shows a warning; a valid file is applied as written, and a deleted file means
defaults. Right after a start there is nothing to keep, so the default applies, with two exceptions that
fail safe instead:

- **Web login** (`auth_user`, `auth_password`): while the file cannot say whether a login is set, the web
  interface answers 503 on everything except `/api/health`, until the file is repaired or deleted. Not on
  Umbrel, where Umbrel's own login protects the dashboard.
- **Spam rules** (`carrier_policy`, `dust_start_height`): if the defaults differ from the rules the stored
  results were made with, the scan stops with an error and the results are kept until the file is repaired.
  (Deleting the file selects the default rules, which rescans.)

Settings given on the command line or in the environment are not affected by the file.

Special environment variables (not settings): `BMV_DATA_DIR` (data directory) and `BMV_MANAGED_BY`, set by
platform packages. With any value the platform's proxy fronts the web interface (Host check and login are left
to it) and plain-HTTP RPC on its internal network is not flagged. `startos` additionally shows the settings
read-only and refers to the service's Actions; with `umbrel` they stay editable in the web interface, because
Umbrel has no settings screen for apps.

> **Secrets:** prefer the cookie file, the environment or `settings.json` (written with mode 0600) over
> `--rpc-password` on the command line, which other local users can see in the process list.

## Node connection

| Setting | Default | Description |
|---|---|---|
| `rpc_url` | http://127.0.0.1:8332 | Bitcoin Core/Knots/Roots RPC URL. |
| `rpc_user` | empty | RPC username (leave empty when using a cookie file). |
| `rpc_password` | empty | RPC password. *(secret, never shown in the UI)* |
| `rpc_cookie_file` | empty | Path to bitcoind's .cookie file (read-only). |
| `rpc_cafile` | empty | Extra CA certificate (PEM) to trust for https RPC, e.g. Start9 root CA. |
| `tor_proxy` | empty | SOCKS5 proxy for .onion RPC, e.g. 127.0.0.1:9050. |
| `use_rest` | true | Use bitcoind's REST interface if it is already enabled (faster). |
| `rpc_timeout` | 120 | RPC timeout in seconds. |

## Scanning

| Setting | Default | Description |
|---|---|---|
| `speed_profile` | eco | eco = gentlest on the node, full = fastest. One of: eco, balanced, full. |
| `scan_window` | empty | Only scan history during this daily time window, e.g. 01:00-07:00. |
| `timezone` | UTC | Time zone for scan_window (IANA name, e.g. Europe/Berlin). |
| `quick_pass_blocks` | 1000 | Recent blocks analysed first for a quick result. |
| `sample_every` | 100 | Before the full scan, analyse every Nth block of the history for an early whole-chain estimate (0 = off; values below 10 count as 10). Runs once; changing it later has no effect until a rescan. |
| `carrier_policy` | true | Keep oversized OP_RETURNs that are verified payment-protocol envelopes (upstream carrier policy). |
| `dust_start_height` | auto | Height from which P2TR dust counts (auto: 767430 on mainnet, 0 on test networks). |
| `bloom_mb` | 256 | Memory for the spent-output filter (MB). The filter is also saved to disk at this size when the app stops, for a fast restart. |

## Web interface

| Setting | Default | Description |
|---|---|---|
| `bind` | 127.0.0.1 | Address the web interface listens on. |
| `port` | 8338 | Port of the web interface. |
| `auth_user` | empty | Username for the web interface (optional basic auth). |
| `auth_password` | empty | Password for the web interface. *(secret, never shown in the UI)* |
| `tls_cert` | empty | TLS certificate (PEM) to serve the web interface over https. |
| `tls_key` | empty | TLS private key (PEM). |
| `allowed_hosts` | empty | Extra Host names allowed for the web interface (comma separated). |

## General

| Setting | Default | Description |
|---|---|---|
| `log_level` | info | Log verbosity. One of: debug, info, warning, error. |
