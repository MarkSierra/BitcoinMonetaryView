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
2. Tag and push: `git tag v0.1.0 && git push origin v0.1.0`.
3. The *Release image* workflow runs the tests and publishes `ghcr.io/marksierra/bitcoinmonetaryview` for
   x86_64 and ARM64 (tags `0.1.0`, `0.1`, `latest`). After the first release, set the package's visibility to
   public once (GitHub → Packages → bitcoinmonetaryview → Package settings).

By contributing you agree that your contribution is licensed under the AGPL-3.0-or-later.
