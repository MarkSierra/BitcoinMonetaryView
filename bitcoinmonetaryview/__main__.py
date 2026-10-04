# SPDX-License-Identifier: AGPL-3.0-or-later
"""Entry point: python3 -m bitcoinmonetaryview"""

import json
import logging
import os
import signal
import sys
import threading

from . import __version__
from .config import Config, build_arg_parser, default_data_dir, json_schema


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    if args.version:
        print(f"BitcoinMonetaryView {__version__}")
        return 0
    if args.print_settings_schema:
        print(json.dumps(json_schema(), indent=2))
        return 0

    data_dir = os.path.abspath(args.data_dir or default_data_dir())
    os.makedirs(data_dir, mode=0o700, exist_ok=True)
    cli = {k: v for k, v in vars(args).items() if k not in ("data_dir", "print_settings_schema", "check", "version")}
    try:
        config = Config(data_dir, cli=cli)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    logging.basicConfig(level=config.log_level.upper(), stream=sys.stdout,
                        format="%(asctime)s %(levelname)-7s %(message)s")
    log = logging.getLogger("bmv")

    if args.check:
        from .diagnostics import run_connection_test
        ok = True
        for c in run_connection_test(config):
            mark = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}[c["status"]]
            print(f"[{mark}] {c['name']}: {c['message']}")
            if c["hint"]:
                print(f"       → {c['hint']}")
            ok &= c["status"] != "fail"
        return 0 if ok else 1

    from .scanner import Scanner
    from .server import make_server

    log.info("BitcoinMonetaryView %s — read-only: nothing on your node is changed", __version__)
    log.info("Data directory: %s", data_dir)
    if config.managed:
        log.info("Managed by %s: settings are read-only in the web interface", config.managed_by)

    try:
        os.nice(10)     # analysis work yields to everything else on this machine
    except (AttributeError, OSError):
        pass

    scanner = Scanner(config)
    try:
        server = make_server(config, scanner)
    except OSError as e:
        log.error("Cannot start the web interface on %s:%s: %s", config.bind, config.port, e)
        return 1
    scheme = "https" if config.tls_cert and config.tls_key else "http"
    host = config.bind if config.bind not in ("0.0.0.0", "::") else "<this-machine>"
    log.info("Web interface: %s://%s:%s/", scheme, host if ":" not in host else f"[{host}]", config.port)

    stop = threading.Event()

    def shutdown(*_):
        if not stop.is_set():
            stop.set()
            log.info("Shutting down…")
            threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    scanner.start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        scanner.stop()
        scanner.join(15)
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
