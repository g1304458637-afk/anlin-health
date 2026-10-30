import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import text

from .db import engine, Base
from .config import settings, validate_settings
from . import models  # noqa: F401 - registers mappings
from .routes import auth, residents, vitals, questionnaires, reminders, family, visits, admin, reporting, assistant, reports, report_assistant, plans, risk_alerts, appointments


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_settings()
    # Pilot convenience only; production requires Alembic-managed schema migrations.
    if settings.app_env != "production":
        Base.metadata.create_all(bind=engine)
    from .report_worker import worker_loop
    from .plan_worker import worker_loop as plan_worker_loop
    stop = asyncio.Event()
    worker = asyncio.create_task(worker_loop(stop)) if settings.report_worker_enabled and settings.app_env != "test" else None
    plan_worker = asyncio.create_task(plan_worker_loop(stop)) if settings.plan_worker_enabled and settings.app_env != "test" else None
    try:
        yield
    finally:
        stop.set()
        workers = [task for task in (worker, plan_worker) if task]
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)


app = FastAPI(title="社区健康管理 API（试点工程）", version="0.8.0", lifespan=lifespan,
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
    # Page scripts live in external static files and every handler is bound via
    # data-act delegation, so script-src can be strict. style-src keeps
    # 'unsafe-inline' for style="" attributes in generated templates — attribute
    # styles are low-risk and removing them is cosmetic churn, not a win.
    assets = " ".join(settings.csp_asset_origins)
    img_src = f"'self' data: blob:{' ' + assets if assets else ''}"
    media_src = f"'self'{(' ' + assets) if assets else ''}"
    response.headers.setdefault("Content-Security-Policy",
                                f"default-src 'self'; img-src {img_src}; media-src {media_src}; style-src 'self' 'unsafe-inline'; script-src 'self'")
    if settings.app_env == "production":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers.setdefault("Cache-Control", "no-store")
    return response


for router in [auth.router, residents.router, vitals.router, questionnaires.router, reminders.router, family.router, visits.router, admin.router, reporting.router, assistant.router, reports.router, report_assistant.router, plans.router, risk_alerts.router, appointments.router]:
    app.include_router(router)

class NoCacheStaticFiles(StaticFiles):
    """Static assets carry an ETag; no-cache makes browsers revalidate so a
    deploy never leaves users on a stale JS bundle (cheap 304 when unchanged)."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", NoCacheStaticFiles(directory=Path(__file__).resolve().parents[1] / "static"), name="static")


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/app")


@app.get("/healthz", tags=["operations"])
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "service": "community-health", "version": "0.8.0"}


@app.get("/console", include_in_schema=False)
def console():
    # Page HTML must revalidate every load: it wires the external JS bundles.
    return FileResponse(Path(__file__).resolve().parents[1] / "static" / "console.html",
                        headers={"Cache-Control": "no-store"})


@app.get("/app", include_in_schema=False)
def portal():
    return FileResponse(Path(__file__).resolve().parents[1] / "static" / "portal.html",
                        headers={"Cache-Control": "no-store"})


@app.get("/help", include_in_schema=False)
def help_page():
    return FileResponse(Path(__file__).resolve().parents[1] / "static" / "help.html",
                        headers={"Cache-Control": "no-store"})
