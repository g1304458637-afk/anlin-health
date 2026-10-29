"""Check served inline and standalone JavaScript with Node."""

from html.parser import HTMLParser
from pathlib import Path
import subprocess


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        if tag != "script":
            return
        values = dict(attrs)
        self.active = "src" not in values and values.get("type", "text/javascript") in (
            "text/javascript", "application/javascript", "module")
        if self.active:
            self.blocks.append("")

    def handle_data(self, data):
        if self.active:
            self.blocks[-1] += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False


def main():
    static = Path(__file__).resolve().parents[1] / "static"
    total = 0
    for name in ("console.html", "portal.html"):
        parser = Scripts()
        parser.feed((static / name).read_text(encoding="utf-8"))
        for index, block in enumerate(parser.blocks, 1):
            result = subprocess.run(["node", "--check"], input=block, text=True, encoding="utf-8",
                                    capture_output=True, check=False)
            if result.returncode:
                raise SystemExit(f"{name} inline script {index}: {result.stderr}")
            total += 1
    for source in sorted(static.glob("*.js")) + sorted((static / "js").glob("*.js")):
        subprocess.run(["node", "--check", str(source)], check=True)
    print(f"Checked {total} inline JavaScript blocks and standalone scripts")


if __name__ == "__main__":
    main()
