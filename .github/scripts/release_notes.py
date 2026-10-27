"""Print the release notes for one version: its CHANGELOG.md section with wrapped lines joined
(GitHub shows every line break in release notes), under a "Highlights" heading."""
import re
import sys

version, path = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "CHANGELOG.md"
section, on = [], False
for line in open(path, encoding="utf-8"):
    if re.match(r"## \[", line):
        if on:
            break
        on = line.startswith(f"## [{version}]")
        continue
    if on:
        section.append(line.rstrip("\n"))
paragraphs = []
for line in section:
    if not line.strip():
        paragraphs.append("")
    elif paragraphs and paragraphs[-1] and not re.match(r"\s*([-*+] |\d+\. |#|\||```)", line):
        paragraphs[-1] += " " + line.strip()          # wrapped continuation of the previous line
    else:
        paragraphs.append(line)
body = "\n".join(paragraphs).strip()
if not body:
    sys.exit(f"CHANGELOG.md has no section for {version}")
print("## Highlights\n\n" + body)
