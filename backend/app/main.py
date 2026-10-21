from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import text

from .db import engine, Base
from .config import settings, validate_settings
from . import models  # noqa: F401 - registers mappings
from .routes import auth, residents, vitals, questionnaires, reminders, family, visits, admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_settings()
    # Pilot convenience only; production requires Alembic-managed schema migrations.
    if settings.app_env != "production":
        Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="社区健康管理 API（试点工程）", version="0.2.0", lifespan=lifespan,
    description="供社区和居民使用的健康记录与日常关怀试点系统。非医疗诊断产品。")
if settings.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False,
                       allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])
if settings.app_env == "production" and settings.allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    # console.html ships inline script/handlers; if you extract them to files, drop 'unsafe-inline'.
    assets = " ".join(settings.csp_asset_origins)
    img_src = f"'self' data:{' ' + assets if assets else ''}"
    media_src = f"'self'{(' ' + assets) if assets else ''}"
    response.headers.setdefault("Content-Security-Policy",
                                f"default-src 'self'; img-src {img_src}; media-src {media_src}; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
    if settings.app_env == "production":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers.setdefault("Cache-Control", "no-store")
    return response


for router in [auth.router, residents.router, vitals.router, questionnaires.router, reminders.router, family.router, visits.router, admin.router]:
    app.include_router(router)


@app.get("/healthz", tags=["operations"])
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "service": "community-health", "version": "0.2.0"}


@app.get("/console", include_in_schema=False)
def console():
    return FileResponse(Path(__file__).resolve().parents[1] / "static" / "console.html")
