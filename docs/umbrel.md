# Umbrel package

Monetary View is packaged for umbrelOS in two places:

- the community app store
  [MarkSierra/umbrel-community-app-store](https://github.com/MarkSierra/umbrel-community-app-store)
  (app ID `marksierra-bitcoinmonetaryview`);
- the official Umbrel App Store (app ID `bitcoinmonetaryview`), submitted from the fork
  [MarkSierra/umbrel-apps](https://github.com/MarkSierra/umbrel-apps) (branch `bitcoinmonetaryview`) in
  [getumbrel/umbrel-apps#6328](https://github.com/getumbrel/umbrel-apps/pull/6328). It is the same package
  without the store prefix, icon and gallery (Umbrel hosts those itself).
 The package is two files, `umbrel-app.yml` and
`docker-compose.yml`, and uses the multi-arch image `ghcr.io/marksierra/bitcoinmonetaryview`, pinned by
version tag and digest.

| Topic | How it is done |
|---|---|
| **Bitcoin node** | `dependencies: [bitcoin]`. Umbrel's Bitcoin Node app and Bitcoin Knots (which implements `bitcoin`) both export `APP_BITCOIN_NODE_IP`, `APP_BITCOIN_RPC_PORT`, `APP_BITCOIN_RPC_USER` and `APP_BITCOIN_RPC_PASS`; the compose file passes them as `BMV_RPC_URL`, `BMV_RPC_USER`, `BMV_RPC_PASSWORD`. Only the whitelisted read-only RPC methods are used; nothing on the node is changed. |
| **Platform mode** | `BMV_MANAGED_BY=umbrel`: Umbrel's `app_proxy` fronts the web UI and provides the login, the Host check is left to it, and plain HTTP on Umbrel's internal Docker network is not flagged. Settings stay editable in the dashboard (Umbrel has no settings screen for apps); the connection settings come from Umbrel and are shown as "set by Umbrel". A damaged `settings.json` does not lock the dashboard here (the login is Umbrel's), but it cannot be saved over from the dashboard: repair or delete it. |
| **Data** | `${APP_DATA_DIR}/data/app` is mounted at `/data`. `storage.dataRoot: data` lets umbrelOS 2.0 move it to another drive. |
| **Backups** | `backupIgnore` excludes the per-network directories (`data/app/mainnet`, …) with the scan database and the saved spent-output filter; only `settings.json` is backed up. The scan can always be rebuilt from the node. |
| **User** | The container runs as `1000:1000` (Umbrel's app user), with a read-only root filesystem, `/tmp` as tmpfs, all capabilities dropped and `no-new-privileges`. |
| **Languages** | The Umbrel app store only takes English for the description and release notes, so those stay in English. The dashboard itself is in English, German, Spanish, French and Polish and follows the browser's language. |
| **Port** | `8338` (the app's default; not used by any app in the official Umbrel store). |

## Updating the package

After a release of this repository (image `X.Y.Z` on GHCR):

1. Get the digest: `docker buildx imagetools inspect ghcr.io/marksierra/bitcoinmonetaryview:X.Y.Z`
   (check that `linux/amd64` and `linux/arm64` are listed).
2. In the store repo, set `image: ghcr.io/marksierra/bitcoinmonetaryview:X.Y.Z@sha256:<digest>` in
   `marksierra-bitcoinmonetaryview/docker-compose.yml`, and `version` / `releaseNotes` in its `umbrel-app.yml`.
3. Commit to `main`. Umbrel devices that added the store offer the update automatically.
4. Official store: make the same change in `bitcoinmonetaryview/` on a branch of the `umbrel-apps` fork
   (synced from `getumbrel/umbrel-apps` first) and open a pull request there.

## Testing on umbrelOS

The *umbrelOS* workflow (`.github/workflows/umbrel.yml`) runs the official `ghcr.io/getumbrel/umbrelos`
image as a container on amd64 and arm64, installs Umbrel's Bitcoin Node and switches it to regtest, mines
blocks with spam, installs both packages through Umbrel (the official one copied into umbrelOS's official
store directory, as Umbrel's testing guide does), opens the app through Umbrel's port in Chromium, restarts
it and checks that the scan persists. It also runs Umbrel's own `lint:apps --check-images` on the official
package. It runs on pull requests that change it and on demand (inputs: umbrelOS version, node app,
repository and branch of the official package). Screenshots and logs are uploaded as artifacts.
