# Packaging notes: StartOS 0.4 (and similar platforms)

This repository is the platform-independent core. A StartOS package is a thin wrapper (a separate
`startos/` project using the StartOS SDK) around the Docker image built from this repo's `Dockerfile`.
Based on the [StartOS 0.4.0.x packaging guide](https://docs.start9.com/packaging/0.4.0.x/).

| StartOS concept | How this app supports it |
|---|---|
| **Settings via Actions + File Model** | Actions write `/data/settings.json` (`merge()`). The app reads it with the schema in [settings.md](settings.md) (`--print-settings-schema` prints JSON Schema). Invalid or missing values fall back to defaults (like zod `.catch()`), unknown keys are ignored, and changes are applied live. |
| **Managed mode** | Set `BMV_MANAGED_BY=startos`. The web UI then shows settings read-only ("managed by StartOS") and disables settings changes and the rescan button. Pause/resume stays (disable with `allow_pause_when_managed=false`). |
| **Dependency on Bitcoin Core / Knots** | `setupDependencies`: `kind: 'running'` plus the node's sync health check. RPC URL: `http://bitcoind.startos:8332` (internal network, no "plain HTTP" warning for `*.startos`). |
| **Credentials** | Mount only the node's RPC cookie file, read-only: `mountDependency({ dependencyId: 'bitcoind', volumeId: 'main', subpath: '<path>/.cookie', mountpoint: '/node/.cookie', readonly: true, type: 'file' })` and set `rpc_cookie_file=/node/.cookie`. The app re-reads the cookie when bitcoind restarts. |
| **Web UI interface** | Bind the app's port (8338) as a `ui` interface. Authentication can be enforced by the StartOS reverse proxy (`addSsl.auth`); the app's own basic auth can stay off in managed mode. The Host-header check is delegated to the proxy in managed mode. |
| **Health checks** | Daemon readiness: `GET /api/health` (cheap). Standalone check "Scan progress": read `GET /api/status` → `phase` (`full`/`quick` → `loading` with `progress` %, `live` → `success`, `error` → `failure`). Use a cooldown trigger (e.g. 30 s). |
| **Logs** | Plain lines on stdout. |
| **Volumes & backups** | One volume (`/data`). Back up `settings.json`; the per-network databases (`/data/<network>/bmv.sqlite*`) can be rebuilt by rescanning and may be excluded to keep backups small. |
| **Architectures** | Pure Python: `x86_64`, `aarch64` (and `riscv64` where the base image exists). |

Recommended action set for the wrapper: *Node connection* (Core or Knots dependency), *Scan speed*
(`speed_profile`), *Scan window* (`scan_window`, `timezone`), *Carrier policy* (`carrier_policy` — changing it
triggers a rescan; say so in the action description), *Rescan* (deletes `/data/<network>/bmv.sqlite*` while
the service is stopped).
