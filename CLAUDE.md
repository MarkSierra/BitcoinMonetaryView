# Working notes for Claude

Monetary View (repo name BitcoinMonetaryView): a read-only dashboard that analyses a Bitcoin Core, Knots or
Roots node with the Monetary Node spam rules. AGPL-3.0-or-later. Independent of the Monetary Node project.

## Ground rules from the maintainer

- Strictly read-only towards bitcoind: RPC/REST whitelist, never change the node's configuration, never
  modify block files (not even when reading them directly). Bitcoind's volume is mounted read-only.
- The scan databases are never included in backups.
- Never ask for tokens or passwords in chat.
- Security and standard procedures come first. Test everything before it ships.
- No Claude session links (`claude.ai/code/session_...`) in PR descriptions or commit messages.
- Standing permission: merge your own PRs in the `MarkSierra/*` repos once CI is green, and dispatch
  `release.yml`. Nothing is posted on Start9-Community or getumbrel without the maintainer's own action.

## Repositories

| Repo | Role |
|---|---|
| `MarkSierra/BitcoinMonetaryView` (this one) | the app; releases are tagged here (`vX.Y.Z`), image on ghcr.io |
| `Start9-Community/bitcoinmonetaryview-startos` | the StartOS package. **Start9 builds and publishes only from here.** A fork of the repo below |
| `MarkSierra/BitcoinMonetaryView-startos` | our workspace for the package: sync `main` from the Start9-Community fork, branch, test, then open PRs to the fork. Never release from here |
| `MarkSierra/umbrel-community-app-store` (`marksierra-bitcoinmonetaryview/`) | Umbrel community listing; image pinned by digest. No CI; merge directly |
| `MarkSierra/umbrel-apps` (`bitcoinmonetaryview/`, branch `bitcoinmonetaryview`) | fork of `getumbrel/umbrel-apps` for the official Umbrel App Store; PRs to getumbrel are opened by the maintainer |

## The app

Pure Python standard library (3.10+), no runtime dependencies. Layout of `bitcoinmonetaryview/`:

- `node/`: talking to bitcoind. `rpc.py` checks every call against the `ALLOWED` whitelist before sending;
  `rest.py` reads blocks over REST when enabled; `client.py` is the read-only node view on top of both.
  Blocks come only over RPC/REST: the app never opens block files.
- `rules/`: `monetary_rules.py` applies the vendored upstream rules in `rules/upstream/` (unmodified copies,
  checksums verified in CI: never edit them).
- `scanner.py` (scan loop, retries, status/activity), `worker.py` (block analysis in worker processes, no
  I/O), `analytics.py`, `store.py` (SQLite per
  network: WAL, nodatacow), `server.py` (HTTP API + static dashboard, basic auth, CSP), `config.py`
  (settings.json and environment), `diagnostics.py`.
- `static/`: the dashboard (`app.js`, five languages in `i18n/`).

Tests: `python -m unittest discover -s tests -t . -v` (unit, parity with upstream, security, server).
Real-node tests (`tests.test_regtest`, `BMV_BITCOIND`), btrfs (`tests.test_store_fs`, `BMV_BTRFS_DIR`) and
the browser test (`tests.test_browser`, Playwright) run in CI. `python3 -m tests.demo` serves a demo.

CI (`ci.yml`, 15 jobs): tests on Python 3.10/3.12/3.13/3.14/3.15, regtest against Core 29.1/31.1/32.1,
Knots 29.4.1 and Roots 29.4/31.1, btrfs, browser, docker, docker-riscv64. `umbrel.yml`: umbrelOS runtime
test. `release.yml`: multi-arch image to ghcr.io.

## StartOS release flow (through Start9-Community)

1. App change → PR in this repo → CI green → merge → run `release.yml` → note the image digest.
2. Package: sync `MarkSierra/BitcoinMonetaryView-startos` `main` from the Start9-Community fork (a branch
   cut from an unsynced copy reverts Start9's review changes). Branch with the **final** version
   (`X.Y.Z:0`, or `:N+1` for a package-only change), move the `upstream` submodule, copy the `FROM` line of
   `upstream/Dockerfile`, write release notes in all five languages.
3. **Test build:** a separate branch (`test/X.Y.Z-beta.N`) with one extra commit that only sets the version to
   `X.Y.Z-beta.N:0`; run the **Build** workflow on it (workflow_dispatch) and give the maintainer the
   `.s9pk` artifact link. `X.Y.Z-beta.N:0` < `X.Y.Z:0`, so the published build arrives as a normal update.
   Start9's guide allows a prerelease suffix only on the upstream side, never after the `:` (0.3.13 used
   `0.3.13:0-beta.1`, which built and installed, but is not the documented form).
   The maintainer sideloads and tests: update over the installed version, a clean install, tasks/actions,
   sign-in, health checks. Fixes go on the final-version branch; the next test build is `-beta.N+1`.
   The first test build of a version is `-beta.0` (maintainer's choice, from 0.3.14 on).
4. PR from the final-version branch (never a test branch) to `Start9-Community/bitcoinmonetaryview-startos`
   `main`. This session cannot post or open PRs there (Claude's GitHub App is not installed on that org):
   give the maintainer a prefilled compare link plus the description in a plain code block. Leave
   "Allow edits and access to secrets by maintainers" unchecked.
5. Each merge there publishes to **Community Beta** (`https://community-beta-registry.start9.com`). Promotion
   to the **Community Registry** is the maintainer's call: an issue on the fork (copies the same build).
   Ask there for the Bitcoin category too (categories are set by the registry, not the manifest).
6. After each merge: sync our `main` from the fork; ask the maintainer to delete the merged branches.
7. Umbrel: bump image tag and digest, version and releaseNotes in `umbrel-app.yml` (community store), and
   the same in the official-store package on the fork (PR to getumbrel). Run the *umbrelOS* workflow first.

Reading the fork: `git` works directly; for PR comments/reviews/CI use a helper session that has the fork
attached (read-only).

Practical notes from the first releases through the fork:
- Claude's GitHub App is not installed on Start9-Community: this session and the helper can read the fork but
  cannot comment, review, open PRs or watch PR activity there. The maintainer posts (give texts in a plain
  code block; they copy them on a phone) and opens PRs via a prefilled compare link
  (`.../compare/main...MarkSierra:BitcoinMonetaryView-startos:<branch>?expand=1&title=...`). A PR from our
  branch updates itself on every push; Start9's build checks run on their own. Keep the PR title and
  description in step with the version (the maintainer edits them).
- A helper session for reading the fork: create one when needed (`create_session` with the fork as source,
  read-only instructions; talk to it with `send_message`) and archive it after the merge.
- Watching a PR or issue there: scheduled tasks (minimum interval one hour; two hourly tasks at different
  minutes give ~30 min) that run `git ls-remote` on the fork and ask the helper for comments; delete them
  after the merge. Matt Hill (Start9) usually answers in the US afternoon/evening; helix-a is Start9's AI
  reviewer.
- Promotion: a blank issue on the fork (none of the fork's issue templates fits), stating what was tested on
  which build. Categories (Bitcoin) are set by Start9 on request. Check the Community Registry first: Start9
  sometimes promotes on its own soon after merging (0.3.10:1 and 0.3.12:0 needed no issue).
- Version numbers may skip: a package version that never reached a registry can be replaced by a newer one
  in the same PR (0.3.8:0 became 0.3.10:0); the release notes then cover everything since the last
  published version.
- Bitcoin Roots: the dependency range accepts `>=#roots:29.4:0`, but Roots is not on the Community Registry
  yet, so package descriptions and release notes do not mention it (Matt's request on PR #4). Add the wording
  back once Roots is on community-prod and tested against the Roots package on StartOS. The app itself and
  its README may name Roots.
- `next` in our StartOS workspace is a copy of Start9-Community's own `next` branch; leave it (not ours).
- Release notes follow the guide's format: "Updated Monetary View to X.Y.Z." then short bullets
  (New/Fix/Internal), then a link to the GitHub release, in all five languages (FR: no-break space before
  ":" and ";").

## Umbrel official App Store

- Umbrel requires a runtime test through umbrelOS before a PR. `.github/workflows/umbrel.yml` does it in CI
  (umbrelOS container on amd64/arm64, Bitcoin Node switched to regtest via its `settings.json`
  `{"chain": "regtest"}`, `umbreld client` for install/restart/logs, Playwright through Umbrel's port), plus
  Umbrel's `lint:apps --check-images`.
- Official app IDs have no store prefix; `bitcoinmonetaryview` matches StartOS. No `icon`, `gallery: []`,
  `releaseNotes: ""` for a new app, `submission:` is the PR link. Lint warns about `security_opt`
  (`no-new-privileges`); justify it in the PR as hardening.
- This session cannot fork, comment or open PRs on getumbrel: the maintainer opens the PR from a prefilled
  compare link (leave "Allow edits by maintainers" on there: Umbrel adds the gallery and icon itself).
- umbrelOS 2.0.1 CLI notes: the store is added with `appStore.addRepository`; `docker cp` into the umbrelOS
  container fails for `/home/umbrel/umbrel/...`, pipe a tar through `docker exec` instead.

## Limits of the cloud environment

- Use the cloud environment "Monetary View": it adds docs.start9.com and the two Start9 registries
  (`community-registry.start9.com`, `community-beta-registry.start9.com`). The Default environment blocks them.
- No Docker daemon; Umbrel's lint and third-party node binaries (e.g. a Roots release) are blocked on purpose:
  test those in CI.
- `git push --delete` is refused by the proxy. In this repo GitHub deletes a PR branch on merge; pushing the
  branch again recreates it (use a plain push: `--force-with-lease` fails on the deleted branch).
- GitHub sometimes creates a CI run with no jobs or only some ("No jobs were run" email): re-run it once and
  check that all 15 jobs exist before merging.

## State (2026-11-10)

App 0.3.13 merged on `main` (fixes from a full code and security review: a damaged settings.json fails safe,
weekly instead of daily gettxoutsetinfo, mutated-block check, request handling, actions pinned by commit,
unused code removed); `release.yml` not run yet. Released: v0.3.12. StartOS 0.3.12:0 in the Community Registry
and on Community Beta, Bitcoin category set (Start9 promoted it on its own within hours of merging #7;
promotion issue #8 closed). The maintainer's server runs StartOS 0.4.2 with Knots 29.3. Umbrel community store
0.3.12; official Umbrel App Store getumbrel/umbrel-apps#6328 at 0.3.12, waiting for review. Open: release
0.3.13 and the package updates (StartOS: exclude `.<net>.nocow`/`.<net>.old` from backups, mention the weekly
UTXO-size request in the descriptions); the Umbrel review; Roots wording once Roots is on community-prod.

Idea for a later release (maintainer: "maybe later", not now): a missing RPC cookie file right after a
reboot (the node has not written it yet) shows as a red "Authentication problem" until the 30 s retry
connects; show it as waiting for the node instead (`scanner.py` NodeAuthError, `node/rpc.py`), keeping red
for wrong credentials.

Lessons from 0.3.10:1-0.3.12: a StartOS user's btrfs data drive went read-only during a first full scan
(fsync of a fragmented copy-on-write SQLite file). The app now creates its per-network directory
nodatacow and checkpoints the WAL every 64 MB (`store.py`, CI job `btrfs`). A StartOS migration that must
still run for users skipping a version stays in a historical version file, not in `current.ts`.

## Before a UI release

Measure texts in all five languages at 360–1920 px with Playwright (`PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`,
Python 3.15 venv with playwright 1.63.0, or the cloud image's Node Playwright 1.63.0 via
`NODE_PATH=$(npm root -g)`; CI pins 1.56.0 because of ubuntu-24.04) and send screenshots. The
demo server has a 50-block quick pass, so tiles that wait for it are not representative of mainnet.

## Start9's packaging rules (they hold every registry package to these)

Guide: https://docs.start9.com/packaging (see `versions.md` "Release Notes", `project-structure.md`,
`writing-readmes.md`, `publishing.md`). Read the relevant pages before each PR.

- **Code comments carry one fact, in one line.** History and reasons go in the PR description.
- **Release notes are for users deciding whether to update.** No entries for defects that never reached a
  published community version; no old-behaviour narration. All five languages (en, de, es, fr, pl).
- **`UPDATING.md` is the upstream bump recipe and nothing else.** Our own workflow (the `-beta.N` testing)
  does not go into the package repo, nor into its `AGENTS.md`.
- README follows the guide's section set; no version numbers in it.
- Pre-publish checklist: `tsc --noEmit`, prettier (`--config node_modules/@start9labs/start-sdk/prettier.config.json`),
  pack/build green, tested end-to-end on StartOS (install, start, UI, health checks, uninstall/reinstall).
- The web UI uses the app's own basic auth (`auth_user`/`auth_password` in settings.json); **Set Web
  Password** runs in any state and restarts a running service (an only-stopped action behind a critical
  task deadlocks after an update).
