"""A reproducible local preview; isolated fictional data, no cloud AI calls."""
import os
from pathlib import Path
import secrets
import sys

backend = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend))
os.chdir(backend)
runtime = backend / ".runtime"
runtime.mkdir(exist_ok=True)
os.environ.update(APP_ENV="development", ENABLE_DEMO_AUTH="true", SECRET_KEY=secrets.token_urlsafe(48),
    DATABASE_URL="sqlite:///" + (runtime / "preview.db").as_posix(),
    REPORT_STORAGE_DIR=str(runtime / "reports"), DEEPSEEK_API_KEY="", REPORT_WORKER_ENABLED="false", ANLIN_SHOWCASE_SEED="1")

if __name__ == "__main__":
    from alembic import command
    from alembic.config import Config
    config = Config(str(backend / "alembic.ini"))
    command.upgrade(config, "head")
    from scripts.seed_showcase import seed_showcase
    seed_showcase()
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8765, log_level="info")
