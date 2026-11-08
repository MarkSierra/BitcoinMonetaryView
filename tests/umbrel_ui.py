# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Opens Monetary View through umbrelOS in Chromium (CI only: the umbrelOS test).

Usage: python -m tests.umbrel_ui <umbrel-url> <app-port> <password> <height> <spam-height> <out-dir> <label>
Signs in to umbrelOS, opens the home screen, then the app through Umbrel's own port, waits until the scan is
live at <height>, checks a spam block and saves screenshots and the API answers in <out-dir>.
"""

import json
import os
import sys
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def main(umbrel, port, password, height, spam_height, out, label):
    height, spam_height = int(height), int(spam_height)
    os.makedirs(out, exist_ok=True)
    host = urlsplit(umbrel).hostname
    app_url = f"http://{host}:{port}/"
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        r = ctx.request.post(f"{umbrel}/trpc/user.login", data={"password": password})
        if not r.ok:
            sys.exit(f"umbrelOS login failed: {r.status} {r.text()[:300]}")
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))

        page.goto(umbrel, wait_until="networkidle")
        page.screenshot(path=f"{out}/{label}-umbrel-home.png")

        errors.clear()
        page.goto(app_url, wait_until="domcontentloaded")
        page.wait_for_function("document.title === 'Monetary View'", timeout=60_000)
        if urlsplit(page.url).port != int(port):
            sys.exit(f"app did not open on Umbrel's port {port}: {page.url}")

        def api(path):
            return page.evaluate("p => fetch(p).then(r => r.json())", path)

        deadline = time.time() + 900
        while True:
            st = api("/api/status")
            if st.get("phase") == "live" and st.get("tip") == height:
                break
            if time.time() > deadline:
                sys.exit(f"scan not live at {height}: {json.dumps(st)[:1000]}")
            time.sleep(5)

        block = api(f"/api/block/{spam_height}")
        if not (block.get("op_return", 0) > 0 and block.get("multisig", 0) > 0):
            sys.exit(f"spam block {spam_height} not classified: {json.dumps(block)[:1000]}")
        with open(f"{out}/{label}-api.json", "w") as f:
            json.dump({"status": st, "summary": api("/api/summary"), "spam_block": block}, f, indent=2)

        page.reload(wait_until="networkidle")
        page.wait_for_timeout(3000)
        page.screenshot(path=f"{out}/{label}-dashboard.png", full_page=True)
        browser.close()
    if errors:
        sys.exit("browser errors:\n" + "\n".join(errors))
    print(f"{label}: live at {height}, block {spam_height} classified, screenshots in {out}")


if __name__ == "__main__":
    main(*sys.argv[1:8])
