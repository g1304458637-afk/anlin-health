"""Create a sanitized ZIP of the source workpack with SHA-256 manifest.
Usage from repository root: python ops/make_release.py
"""
from pathlib import Path
import zipfile
import hashlib

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / (ROOT.name + '.zip')
IGNORED = {'.git', '.pytest_cache', '__pycache__', 'node_modules', 'miniprogram_npm', '.venv', '.vscode', '.idea'}
IGNORED_SUFFIXES = {'.pyc', '.db', '.sqlite3', '.log'}


def allowed(path: Path):
    rel = path.relative_to(ROOT)
    return not (any(part in IGNORED for part in rel.parts) or path.name == '.env' or path.suffix.lower() in IGNORED_SUFFIXES or path.name.startswith('.DS_Store'))


def main():
    files = sorted(p for p in ROOT.rglob('*') if p.is_file() and allowed(p) and p.name != 'SHA256SUMS.txt')
    manifest = []
    for f in files:
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        manifest.append(f'{digest}  {f.relative_to(ROOT).as_posix()}')
    (ROOT/'SHA256SUMS.txt').write_text('\n'.join(manifest)+'\n',encoding='utf-8')
    files.append(ROOT/'SHA256SUMS.txt')
    with zipfile.ZipFile(OUT,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for f in sorted(files):
            z.write(f,arcname=f'{ROOT.name}/{f.relative_to(ROOT).as_posix()}')
    with zipfile.ZipFile(OUT) as z:
        corrupt=z.testzip()
        assert corrupt is None, f'Corrupted file: {corrupt}'
    print(f'Created {OUT} ({OUT.stat().st_size:,} bytes, {len(files)} files)')


if __name__=='__main__':main()
