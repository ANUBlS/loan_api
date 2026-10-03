import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .config import settings
from .database import engine
from .errors import register_error_handlers
from .routers import admin, applications, auth, catalog, documents, loans, me, payments

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Backend for the Smart Mobile loan app (Flutter).\n\n"
        "Sign in: `POST /api/v1/auth/otp/request` → `POST /api/v1/auth/otp/verify` → "
        "send `Authorization: Bearer <accessToken>`; renew with `POST /api/v1/auth/refresh`.\n\n"
        "Errors always look like `{\"error\": {\"code\": \"...\", \"message\": \"...\"}}`."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)

API = "/api/v1"
for r in (auth.router, me.router, catalog.router, loans.router, payments.router,
          documents.router, applications.router, admin.router):
    app.include_router(r, prefix=API)


@app.get("/health", tags=["Health"])
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "version": settings.app_version}
