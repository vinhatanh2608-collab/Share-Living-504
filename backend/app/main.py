import asyncio
import httpx
from .core.circuit_breaker import CircuitBreaker
from .services.storage_service import client as storage_client
from contextlib import asynccontextmanager, suppress
from secrets import token_hex
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
import logging
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo import TEXT
from pymongo.errors import PyMongoError
from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError, TimeoutError as PoolTimeout
from .api import (routes_advisor, routes_auth, routes_chat, routes_finance, routes_group_chat,
                  routes_group_finance, routes_living, routes_study, routes_ws)
from .core.flood_guard import FloodGuard
from .core.config import settings
from .core.database import Resources
from .core.request_security import RequestSecurity, is_receipt_path
from .core.security import passwords
from .services.finance_service import deliver_forever
from .services.ws_manager import Manager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    cfg = settings()
    resources = Resources()
    task = None
    try:
        await resources.initialize_schema()
        await resources.mongo.command("ping")
        if app.state.legacy_room:
            await resources.mongo.messages.create_index("sort_key", unique=True)
            await resources.mongo.messages.create_index([("kind", 1), ("sort_key", -1)])
            await resources.mongo.messages.create_index([("audience", 1), ("sort_key", -1)])
            await resources.mongo.materials.create_index([("title", TEXT), ("description", TEXT)],
                                                         default_language="none")
        else:
            await resources.mongo.group_messages.create_index([("group_id", 1), ("created_at", -1), ("_id", -1)])
            await resources.mongo.ai_chat_history.create_index([("user_id", 1), ("_id", -1)])
        app.state.resources = resources
        app.state.ws = Manager()
        app.state.login_slots = asyncio.Semaphore(4)
        app.state.dummy_password = await asyncio.to_thread(passwords.hash, token_hex(32))
        task = asyncio.create_task(deliver_forever(resources, app.state.ws, cfg.owner_username)
                                   if app.state.legacy_room else routes_group_chat.project_forever(resources, app.state.ws))
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=3, write=5, pool=3),
                                     limits=httpx.Limits(max_connections=10), trust_env=False) as http:
            app.state.http = http
            app.state.advisor_breaker = CircuitBreaker()
            storage_client(False)
            storage_client(True)
            yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        try:
            if storage_client.cache_info().currsize:
                for public in (False, True):
                    storage_client(public).close()
                storage_client.cache_clear()
        finally:
            await resources.close()


def create_app(legacy_room=None, config=None):
    cfg = config or settings()
    app = FastAPI(title="Shared Living API", version="2.0.0", lifespan=lifespan)
    app.state.legacy_room = cfg.legacy_room if legacy_room is None else legacy_room
    app.state.cors_origins = cfg.cors_origins
    app.include_router(routes_ws.router)
    app.include_router(routes_auth.router, prefix="/api/v1")
    if app.state.legacy_room:
        app.include_router(routes_auth.router)
        for api in (routes_finance, routes_chat, routes_study):
            app.include_router(api.router)
            app.include_router(api.router, prefix="/api/v1", include_in_schema=False)
        app.include_router(routes_auth.legacy_router)
        app.include_router(routes_auth.legacy_router, prefix="/api/v1", include_in_schema=False)
    else:
        for api in (routes_living, routes_group_finance, routes_group_chat, routes_advisor):
            app.include_router(api.router, prefix="/api/v1")

    @app.middleware("http")
    async def secure_response(request, call_next):
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled backend error for %s %s", request.method, request.url.path)
            response = JSONResponse({"detail": "Internal server error"}, status_code=500)
        return response

    app.add_middleware(CORSMiddleware, allow_origins=cfg.cors_origins,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["Authorization", "Content-Type"], expose_headers=["Retry-After"])
    app.add_middleware(RequestSecurity, config=cfg)
    app.add_middleware(FloodGuard, capacity=cfg.max_inflight_requests, header_limit=cfg.max_header_bytes,
                       cors_origins=cfg.cors_origins)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request, error):
        if is_receipt_path(request.url.path):
            for item in error.errors():
                if item["loc"] == ("body", "content_type"):
                    return JSONResponse({"detail": "Only JPEG, PNG and WebP receipts are allowed"}, status_code=400)
                if item["loc"] == ("body", "size") and item["type"] == "less_than_equal":
                    return JSONResponse({"detail": "Receipt exceeds the upload limit"}, status_code=413)
        fields = ("loc", "msg", "type")
        return JSONResponse({"detail": [{field: item[field] for field in fields} for item in error.errors()]},
                            status_code=422)

    @app.exception_handler(PyMongoError)
    async def handle_mongo(request, error):
        logger.error("Document storage unavailable for %s %s", request.method, request.url.path, exc_info=error)
        return JSONResponse({"detail": "Document storage unavailable"}, status_code=503)

    @app.exception_handler(SQLAlchemyError)
    async def handle_sql(request, error):
        logger.error("Database request failed for %s %s", request.method, request.url.path, exc_info=error)
        if isinstance(error, (OperationalError, InterfaceError, PoolTimeout)) or getattr(error, "connection_invalidated", False):
            return JSONResponse({"detail": "Database temporarily unavailable. Try again shortly."}, status_code=503)
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    @app.get("/health/live", include_in_schema=False)
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready():
        resources = app.state.resources
        pool = resources.engine.sync_engine.pool
        metrics = {"mysql_pool": {"checked_out": pool.checkedout(), "capacity": 25},
                   "mongo_pool": resources.mongo_pool.snapshot()}
        try:
            async with asyncio.timeout(5):
                async with resources.engine.connect() as sql:
                    await sql.execute(text("SELECT 1"))
                await resources.mongo.command("ping")
        except (TimeoutError, SQLAlchemyError, PyMongoError):
            return JSONResponse({"status": "unavailable", **metrics}, status_code=503)
        return {"status": "ok", **metrics}

    return app


app = create_app()
