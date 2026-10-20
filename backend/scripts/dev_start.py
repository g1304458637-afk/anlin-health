"""Launch a localhost-only demo, optionally adding fictional seed data."""
from pathlib import Path
import argparse
import os


def load_env(file: Path):
    if not file.exists():
        print('Warning: no .env found. Copy .env.example to .env first.')
        return
    for raw in file.read_text(encoding='utf-8').splitlines():
        line=raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key,value=line.split('=',1)
        os.environ.setdefault(key.strip(),value.strip().strip('"').strip("'"))


def main():
    parser=argparse.ArgumentParser(description='Start local-only community health demonstration')
    parser.add_argument('--seed',action='store_true',help='Insert fake residents if database is empty')
    parser.add_argument('--port',type=int,default=8000)
    args=parser.parse_args()
    load_env(Path(__file__).resolve().parents[1]/'.env')
    if os.environ.get('APP_ENV','development')=='production':
        raise RuntimeError('dev_start.py must never run in production')
    if args.seed:
        from app.seed import seed
        seed()
    import uvicorn
    uvicorn.run('app.main:app',host='127.0.0.1',port=args.port,log_level='info')


if __name__=='__main__':
    main()
