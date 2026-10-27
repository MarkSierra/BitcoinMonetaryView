# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Browser test: every dashboard page in a real Chromium against the app and a fake node.

Fails on JavaScript errors, console errors (including Content-Security-Policy violations),
failed API calls, requests to anything but the app itself, and horizontal scrolling at
phone width. Needs the Python package `playwright` and a Chromium for it (CI installs
both); skipped otherwise. BMV_CHROMIUM may point to a Chromium binary.
"""

import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.request

try:
    from playwright.sync_api import sync_playwright
except ImportError:            # not a dependency of the app
    sync_playwright = None

from bitcoinmonetaryview.config import Config
from bitcoinmonetaryview.scanner import Scanner
from bitcoinmonetaryview.server import make_server
from tests.demo import era_maker
from tests.mocknode import MockChain, MockNode
from tests.test_server import free_port

PHONE = 375


@unittest.skipUnless(sync_playwright, "playwright not installed")
class Dashboard(unittest.TestCase):
    LOCALE = "en-US"            # the browser's language; the dashboard follows it
    VIEWS = {  # route -> a heading only that page shows
        "#/overview": "Your node stores",
        "#/blocks": "Spam per block",
        "#/history": "Spam per month",
        "#/utxo": "How old they are",
        "#/about": "What counts as spam",
        "#/connection": "Connection test",
    }

    @classmethod
    def setUpClass(cls):
        cls.chain = MockChain()
        mk = era_maker(120)
        for _ in range(120):
            cls.chain.append(mk)
        cls.node = MockNode(cls.chain, chain_name="main")
        cls.dir = tempfile.mkdtemp()
        cls.port = free_port()
        cls.config = Config(cls.dir, cli={
            "rpc_url": cls.node.url, "rpc_user": "u", "rpc_password": "p", "port": str(cls.port),
            "dust_start_height": "0", "speed_profile": "full", "quick_pass_blocks": "20", "bloom_mb": "8"}, env={})
        cls.scanner = Scanner(cls.config)
        cls.srv = make_server(cls.config, cls.scanner)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.scanner.start()
        t = time.time()
        while not (cls.scanner.status.get("phase") == "live" and cls.scanner.status.get("tip") == 119):
            if time.time() - t > 120:
                raise RuntimeError(f"scan did not finish: {cls.scanner.status.snapshot()}")
            time.sleep(0.2)
        cls.pw = sync_playwright().start()
        exe = os.environ.get("BMV_CHROMIUM")
        cls.browser = cls.pw.chromium.launch(executable_path=exe) if exe else cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.scanner.stop()
        cls.scanner.join(15)
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.node.stop()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def open(self, **ctx):
        """A fresh page that records every problem in self.problems."""
        self.problems = []
        context = self.browser.new_context(locale=self.LOCALE, **ctx)
        self.addCleanup(context.close)
        page = context.new_page()
        base = f"http://127.0.0.1:{self.port}"
        page.on("pageerror", lambda e: self.problems.append(f"JavaScript error: {e}"))
        page.on("console", lambda m: m.type == "error" and self.problems.append(f"console error: {m.text}"))
        page.on("requestfailed", lambda r: self.problems.append(f"request failed: {r.url}"))

        def response(r):
            if r.status >= 400:
                self.problems.append(f"HTTP {r.status}: {r.url}")
        page.on("response", response)

        def request(r):
            if not r.url.startswith(base + "/"):
                self.problems.append(f"request outside the app: {r.url}")
        page.on("request", request)
        page.goto(base + "/")
        page.wait_for_selector(".tabs a[aria-current=page]")
        return page

    def visit(self, page, route, heading):
        page.evaluate("r => { location.hash = r }", route)
        page.locator("#main").get_by_text(heading, exact=False).first.wait_for(timeout=15000)
        page.wait_for_load_state("networkidle")
        main = page.inner_text("#main")
        self.assertNotIn(": HTTP ", main, f"{route} shows a failed API call")

    def test_every_page_without_errors(self):
        page = self.open(viewport={"width": 1400, "height": 900})
        for route, heading in self.VIEWS.items():
            with self.subTest(route):
                self.visit(page, route, heading)
        # block detail dialog, opened from the blocks page like a user would
        self.visit(page, "#/blocks", self.VIEWS["#/blocks"])
        page.evaluate("location.hash = '#/block/100'")
        page.locator("#dialog[open]").wait_for(timeout=15000)
        self.assertIn("100", page.inner_text("#dialog"))
        page.keyboard.press("Escape")
        # theme toggle
        before = page.evaluate("document.documentElement.dataset.theme || ''")
        page.click("#theme-toggle")
        self.assertNotEqual(before, page.evaluate("document.documentElement.dataset.theme || ''"))
        self.assertEqual(self.problems, [])

    def test_phone_and_tablet_width_have_no_horizontal_scroll(self):
        for width, mobile in ((PHONE, True), (820, True)):
            page = self.open(viewport={"width": width, "height": 800}, is_mobile=mobile, has_touch=mobile)
            for route, heading in self.VIEWS.items():
                with self.subTest(width=width, route=route):
                    self.visit(page, route, heading)
                    # a phone widens its layout to fit too-wide content, so compare with the screen width
                    overflow = page.evaluate("document.documentElement.scrollWidth") - width
                    self.assertLessEqual(overflow, 0, f"{route} scrolls sideways by {overflow}px at {width}px")
            self.assertEqual(self.problems, [])

    def test_header_does_not_move_between_pages(self):
        """A short page without a scrollbar must not shift the centred layout (headless Chromium
        hides scrollbars by default, so this needs a browser that shows them)."""
        exe = os.environ.get("BMV_CHROMIUM")
        browser = self.pw.chromium.launch(executable_path=exe, ignore_default_args=["--hide-scrollbars"])
        self.addCleanup(browser.close)
        saved, self.browser = self.browser, browser
        try:
            page = self.open(viewport={"width": 1400, "height": 900})
        finally:
            self.browser = saved
        spots, scrolls = {}, set()
        for route, heading in self.VIEWS.items():
            self.visit(page, route, heading)
            spots[route] = page.evaluate("[...document.querySelectorAll('.brand, .tabs, #theme-toggle, main')]"
                                         ".map(e => Math.round(e.getBoundingClientRect().left * 10) / 10)")
            scrolls.add(page.evaluate("document.documentElement.scrollHeight > innerHeight"))
        self.assertEqual(scrolls, {True, False}, "needs pages with and without a scrollbar")
        self.assertEqual(len(set(map(tuple, spots.values()))), 1, spots)
        self.assertEqual(self.problems, [])

    def test_language(self):
        page = self.open(viewport={"width": 1400, "height": 900})
        self.assertEqual(page.evaluate("document.documentElement.lang"), self.LOCALE[:2])
        other = "de" if self.LOCALE.startswith("en") else "en"
        self.assertEqual(page.inner_text("#lang-toggle").lower(), other)
        page.click("#lang-toggle")                       # switches, remembers, reloads
        page.wait_for_selector(f"html[lang={other}]")      # (wait_for_function needs eval, which the CSP forbids)
        page.wait_for_selector(".tabs a[aria-current=page]")
        self.assertEqual(page.inner_text(".tabs a[data-view=overview]"), "Übersicht" if other == "de" else "Overview")
        self.assertEqual(self.problems, [])

    def test_api_reachable(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/health", timeout=5) as r:
            self.assertEqual(r.status, 200)


class DashboardGerman(Dashboard):
    """The same checks with a German browser: German texts are longer, and every page must still fit."""
    LOCALE = "de-DE"
    VIEWS = {
        "#/overview": "Dein Node speichert",
        "#/blocks": "Spam pro Block",
        "#/history": "Spam pro Monat",
        "#/utxo": "Wie alt sie sind",
        "#/about": "Was als Spam zählt",
        "#/connection": "Verbindungstest",
    }


if __name__ == "__main__":
    unittest.main()
