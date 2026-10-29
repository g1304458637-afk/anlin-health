"""One-shot migration: split page assets out of portal.html/console.html.

Extracts the inlined Inter fonts, page CSS and page JS into cacheable static
files so the CSP can drop script-src 'unsafe-inline'. Keeps this script in the
repo as documentation of how the split was performed.
"""
import base64
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
FONT_WEIGHTS = [400, 500, 600]


def extract_fonts(html: str) -> str:
    """Pull the three @font-face data URLs into static/fonts/*.woff2; return CSS without them."""
    faces = re.findall(r"@font-face\{[^}]*?font-weight:(\d+);.*?base64,([A-Za-z0-9+/=]+)\)", html)
    assert faces, "font-face blocks not found"
    fonts_dir = STATIC / "fonts"
    fonts_dir.mkdir(exist_ok=True)
    seen = {}
    for weight, payload in faces:
        seen[int(weight)] = payload  # identical across pages; last wins
    assert sorted(seen) == FONT_WEIGHTS, f"unexpected weights {sorted(seen)}"
    for weight, payload in seen.items():
        (fonts_dir / f"inter-{weight}.woff2").write_bytes(base64.b64decode(payload))
    css = fonts_dir / "fonts.css"
    css.write_text("".join(
        f"@font-face{{font-family:'Inter';font-style:normal;font-weight:{w};font-display:swap;"
        f"src:url('/static/fonts/inter-{w}.woff2') format('woff2')}}\n" for w in FONT_WEIGHTS), encoding="utf-8")
    stripped = re.sub(r"@font-face\{[^}]*?base64,[A-Za-z0-9+/=]+\)\s*format\('woff2'\)\}", "", html)
    return stripped


def process(name: str, page_js: list[str]) -> None:
    html = (STATIC / f"{name}.html").read_text(encoding="utf-8")
    html = extract_fonts(html)
    style_blocks = re.findall(r"<style>([\s\S]*?)</style>", html)
    css = "\n".join(b.strip("\n") for b in style_blocks if b.strip())
    css = re.sub(r"\n{3,}", "\n\n", css)
    (STATIC / f"{name}.css").write_text(css + "\n", encoding="utf-8")
    html = re.sub(r"[ \t]*<style>[\s\S]*?</style>\n?", "", html)

    scripts = re.findall(r"<script>([\s\S]*?)</script>", html)
    assert len(scripts) == len(page_js), f"{name}: expected {len(page_js)} inline scripts, found {len(scripts)}"
    js = ("\n\n".join(s for s, keep in zip(scripts, page_js, strict=True) if keep)).strip() + "\n"
    (STATIC / "js" / f"{name}-app.js").write_text(js, encoding="utf-8")
    html = re.sub(r"[ \t]*<script>[\s\S]*?</script>\n?", "", html)

    core = "\n<script defer src=\"/static/js/app-core.js\"></script>" \
           f"\n<script defer src=\"/static/js/{name}-app.js\"></script>"
    html = html.replace("</head>", f'<link rel="stylesheet" href="/static/fonts/fonts.css">\n'
                                   f'<link rel="stylesheet" href="/static/{name}.css">{core}\n</head>')
    (STATIC / f"{name}.html").write_text(html, encoding="utf-8")
    size = (STATIC / f"{name}.html").stat().st_size
    print(f"{name}: html {size//1024} KB, css {len(css)//1024} KB, js {len(js)//1024} KB")


if __name__ == "__main__":
    process("portal", page_js=[True])
    process("console", page_js=[True, True])
