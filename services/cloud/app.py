"""Public infrastructure health only. Deliberately no local pilot routes."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import CloudSettings
from .database import ReadinessProbe


class SafeResponses:
    """No request values/tracebacks in error responses or application logs."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def safe_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                ]
            await send(message)

        try:
            await self.app(scope, receive, safe_send)
        except Exception:
            if not started:
                await JSONResponse({"status": "unavailable", "code": "INTERNAL_ERROR"}, status_code=500)(scope, receive, safe_send)


def create_app(settings: CloudSettings | None = None, probe: ReadinessProbe | None = None) -> FastAPI:
    settings = settings or CloudSettings.from_env()
    readiness = probe or ReadinessProbe(settings)
    app = FastAPI(
        title="AisleSignals cloud infrastructure",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        debug=False,
        redirect_slashes=False,
    )

    @app.get("/")
    async def index():
        return {
            "service": "AisleSignals cloud staging",
            "stage": "infrastructure-only",
            "customer_access": False,
            "device_enrolment": False,
            "event_sync": False,
            "cctv_processing": "on-laptop",
            "database_readiness": "/health/ready",
        }

    @app.get("/health/live")
    async def live():
        return {"status": "alive", "stage": "infrastructure-only"}

    @app.get("/health/ready")
    async def ready():
        result = await readiness.check()
        return JSONResponse(
            {"status": "ready" if result.ready else "unavailable", "code": result.code, "scope": "database-schema-only"},
            status_code=200 if result.ready else 503,
            headers={} if result.ready else {"Retry-After": "2"},
        )

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts), www_redirect=False)
    app.add_middleware(SafeResponses)
    return app
