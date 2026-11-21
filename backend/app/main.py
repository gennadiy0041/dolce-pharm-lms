"""FastAPI-приложение: REST API для обеих ролей и раздача собранного клиента (PWA)."""
from __future__ import annotations

import io
import logging
import mimetypes
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config
from .routers import assessments, auth, courses, files, learner, reports, users
from .security import Principal, require_methodist
from .services import netinfo

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".mjs")

log = logging.getLogger("lms")


def _setup_logging() -> None:
    config.ensure_dirs()
    if any(isinstance(h, RotatingFileHandler) for h in log.handlers):
        return
    handler = RotatingFileHandler(config.LOG_DIR / "lms.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


_setup_logging()

app = FastAPI(
    title="Dolce Pharm LMS",
    version="1.0.0",
    description="Единый REST API учебной платформы: кабинет методиста, кабинет сотрудника, синхронизация "
                "автономных устройств, отчёты и аттестационные документы.",
    docs_url=None, redoc_url=None, openapi_url="/api/openapi.json",
)

for r in (auth.router, users.router, courses.router, files.router, learner.router, reports.router, assessments.router):
    app.include_router(r)

CSP = ("default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "media-src 'self' blob:; font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; "
       "frame-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'")
PLAIN_HTTP_ALLOWED = ("/phone", "/ca.crt", "/api/health")


@app.middleware("http")
async def security_layer(request: Request, call_next):
    path = request.url.path
    client = request.client.host if request.client else ""
    local = client in ("127.0.0.1", "::1", "localhost", "testclient")
    # По сети без шифрования доступна только страница подключения телефона: пароли и ответы
    # сотрудников передаются исключительно по HTTPS.
    if request.url.scheme == "http" and not local and not path.startswith(PLAIN_HTTP_ALLOWED):
        host = request.url.hostname or client
        if path.startswith("/api/"):
            return JSONResponse({"detail": {"code": "https_required", "message": "Используйте адрес с https://"}}, status_code=400)
        return RedirectResponse("https://%s:%d%s" % (host, config.HTTPS_PORT, path), status_code=307)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    if not path.startswith("/api/docs"):
        response.headers.setdefault("Content-Security-Policy", CSP)
    # Strict-Transport-Security здесь не выставляется намеренно: браузер запомнил бы правило для «localhost»
    # и перестал открывать http://localhost на ноутбуке методиста. В промышленной установке заголовок
    # добавляет обратный прокси с настоящим доменным именем.
    if path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    if not isinstance(detail, dict):
        detail = {"message": str(detail)}
    return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Необработанная ошибка %s %s", request.method, request.url.path)
    return JSONResponse({"detail": {"code": "server_error", "message": "Внутренняя ошибка сервера. Запись в журнале."}}, status_code=500)


# ---------- служебное ----------

@app.get("/api/docs", include_in_schema=False)
def api_docs() -> HTMLResponse:
    return get_swagger_ui_html(openapi_url="/api/openapi.json", title="Dolce Pharm LMS — API",
                               swagger_js_url="/swagger/swagger-ui-bundle.js", swagger_css_url="/swagger/swagger-ui.css",
                               swagger_favicon_url="/favicon.svg")


@app.get("/swagger/{name}", include_in_schema=False)
def swagger_asset(name: str):
    path = config.ASSETS_DIR / "swagger" / Path(name).name
    if not path.exists():
        raise HTTPException(404, detail={"message": "Документация API не установлена"})
    return FileResponse(path)


@app.get("/api/system/info", summary="Адреса для подключения телефона и сведения об установке", tags=["Система"])
def system_info(p: Principal = Depends(require_methodist)) -> dict:
    urls = netinfo.lan_urls()
    return {
        "env": config.ENV, "https_port": config.HTTPS_PORT, "http_port": config.HTTP_PORT,
        "lan": [{"ip": u["ip"], "stable": u["stable"], "app_url": u["app_url"], "setup_url": u["setup_url"],
                 "qr_app": netinfo.qr_svg(u["app_url"]), "qr_setup": netinfo.qr_svg(u["setup_url"])} for u in urls],
        "stable_url": "https://%s:%d/" % (netinfo.HOTSPOT_IP, config.HTTPS_PORT),
        "data_dir": str(config.DATA_ROOT), "files_dir": str(config.FILES_DIR), "backup_dir": str(config.BACKUP_DIR),
        "log_dir": str(config.LOG_DIR), "database": "встроенный PostgreSQL (порт %d)" % config.PG_PORT
        if config.EMBEDDED_PG else "внешний PostgreSQL", "tz": config.TZ_LABEL,
        "converter": netinfo.converter_name(),
    }


@app.get("/ca.crt", include_in_schema=False)
def ca_certificate():
    path = config.CERT_DIR / "dolce-lms-root-ca.crt"
    if not path.exists():
        raise HTTPException(404, detail={"message": "Корневой сертификат ещё не создан — запустите сервер через run.py"})
    return FileResponse(path, media_type="application/x-x509-ca-cert", filename="dolce-lms-root-ca.crt")


@app.get("/phone", include_in_schema=False)
def phone_setup(request: Request) -> HTMLResponse:
    host = request.url.hostname or "localhost"
    return HTMLResponse(netinfo.phone_page(host))


# ---------- клиент (PWA) ----------

NO_CACHE = {"Cache-Control": "no-cache"}


def _static(path: Path) -> Response:
    name = path.name
    if path.parent.name == "assets":
        headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    else:
        headers = dict(NO_CACHE)
    if name == "sw.js":
        headers["Service-Worker-Allowed"] = "/"
    return FileResponse(path, headers=headers)


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(404, detail={"message": "Нет такого адреса API"})
    dist = config.FRONTEND_DIST
    if not (dist / "index.html").exists():
        return HTMLResponse("<h1>Клиент не собран</h1><p>Выполните <code>setup.bat</code> или "
                            "<code>npm run build</code> в папке frontend.</p>", status_code=503)
    if full_path:
        candidate = (dist / full_path).resolve()
        if dist.resolve() in candidate.parents and candidate.is_file():
            return _static(candidate)
        if "." in Path(full_path).name:        # запрошен несуществующий файл, а не экран приложения
            raise HTTPException(404, detail={"message": "Файл не найден"})
    return FileResponse(dist / "index.html", headers=NO_CACHE)
