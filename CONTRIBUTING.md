# Contributing

Thanks for helping! A few ground rules keep this project trustworthy:

1. **Read-only stays read-only.** Never add an RPC method to the whitelist that can change node state, and
   never add code that writes outside the app's data directory. `tests/test_server.py::StaticSafety` enforces
   part of this; reviewers check the rest.
2. **Standard library only.** No third-party Python packages, no CDNs or external requests in the web UI.
3. **Upstream rules are not edited.** Files in `bitcoinmonetaryview/rules/upstream/` are byte-identical copies of
   the Monetary Node project. To update them, copy the new upstream files, update the commit in `NOTICE` and
   `bitcoinmonetaryview/rules/__init__.py`, run the tests (parity and upstream suites) and note it in the
   changelog. Changed rules trigger an automatic rescan for users.
4. **Tests**: `python3 -m unittest discover -s tests -t .` must pass. Add tests for new behaviour.
5. **English** for code, UI, docs and commit messages.

## Releases

1. Update the version in `bitcoinmonetaryview/__init__.py` and `pyproject.toml`, and the date in `CHANGELOG.md`.
2. Merge that change into `main`, then create the release on GitHub (Releases → *Draft a new release* → new
   tag `vX.Y.Z` on `main` → *Generate release notes* → *Publish*), or tag from the command line:
   `git tag -a vX.Y.Z -m "BitcoinMonetaryView X.Y.Z" && git push origin vX.Y.Z`.
3. The *Release image* workflow runs the tests and publishes `ghcr.io/marksierra/bitcoinmonetaryview` for
   x86_64 and ARM64 (tags `X.Y.Z`, `X.Y`, `latest`). The package is public.
4. StartOS: in [BitcoinMonetaryView-startos](https://github.com/MarkSierra/BitcoinMonetaryView-startos), move the
   `upstream` submodule to the release, bump the package version and release notes in
   `startos/versions/current.ts`, and run its *Build* workflow (see its `UPDATING.md`).

By contributing you agree that your contribution is licensed under the AGPL-3.0-or-later.
