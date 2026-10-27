# SPDX-License-Identifier: AGPL-3.0-or-later
"""The release workflow turns the current version's CHANGELOG.md section into release notes."""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, ".github", "scripts", "release_notes.py")


def notes(version, changelog):
    return subprocess.run([sys.executable, "-I", SCRIPT, version, changelog], capture_output=True, text=True)


class ReleaseNotes(unittest.TestCase):
    def test_current_version_has_notes_without_hard_wraps(self):
        from bitcoinmonetaryview import __version__
        r = notes(__version__, os.path.join(ROOT, "CHANGELOG.md"))
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "## Highlights")
        self.assertTrue(any(l.startswith("- ") for l in lines))
        self.assertFalse([l for l in lines if l.startswith(" ")], "wrapped lines must be joined")

    def test_joins_wrapped_lines_and_keeps_structure(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("# Changelog\n\n## [1.1.0] — 2026-01-02\n\nIntro that is\nwrapped.\n\n"
                    "- First item\n  continues here.\n- Second item\n\n## [1.0.0] — 2026-01-01\n\n- Old\n")
        try:
            r = notes("1.1.0", f.name)
            self.assertEqual(r.stdout, "## Highlights\n\nIntro that is wrapped.\n\n"
                                       "- First item continues here.\n- Second item\n")
            self.assertNotEqual(notes("9.9.9", f.name).returncode, 0)
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
