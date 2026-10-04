# Settings reference

Every setting can be given in three ways (highest precedence first):

1. command line: `--rpc-url http://…` (underscores become dashes)
2. environment: `BMV_RPC_URL=http://…`
3. `settings.json` in the data directory: `{"rpc_url": "http://…"}`

`settings.json` is **self-healing**: unknown keys are ignored, and a missing or invalid value falls back to its
default with a warning in the log — a bad value never crashes the app. Changes to the file are picked up
while the app runs. This is the file a platform package (e.g. a StartOS action) writes.

The machine-readable JSON Schema is printed by `python3 -m bitcoinmonetaryview --print-settings-schema`.

Special environment variables (not settings): `BMV_DATA_DIR` (data directory) and `BMV_MANAGED_BY`
(e.g. `startos` — the web interface then shows settings read-only and refers to the platform).

> **Secrets:** prefer the cookie file, the environment or `settings.json` (written with mode 0600) over
> `--rpc-password` on the command line, which other local users can see in the process list.

## Node connection

| Setting | Default | Description |
|---|---|---|
| `rpc_url` | http://127.0.0.1:8332 | Bitcoin Core/Knots RPC URL. |
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
| `bloom_mb` | 256 | Memory for the spent-output filter (MB). |

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
| `allow_pause_when_managed` | true | Keep the pause/resume button in managed mode. |

## General

| Setting | Default | Description |
|---|---|---|
| `log_level` | info | Log verbosity. One of: debug, info, warning, error. |
