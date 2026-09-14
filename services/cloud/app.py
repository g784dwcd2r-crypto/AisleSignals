"""AisleSignals same-origin management console, isolated from local CCTV APIs."""

from pathlib import Path
import io
import zipfile

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import CloudSettings
from .database import ReadinessProbe

WEB_DIST = Path(__file__).resolve().parents[2] / 'apps' / 'control' / 'dist'
MAX_BODY = 65536


class SafeResponses:
    """Bounded bodies and private responses, without request/secret tracebacks."""

    def __init__(self, app: ASGIApp, staging: bool = False):
        self.app, self.staging = app, staging

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        started = False

        async def safe_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
                existing = [(key, value) for key, value in message.get('headers', []) if key.lower() not in {b'cache-control', b'content-security-policy'}]
                message['headers'] = existing + [
                    (b'cache-control', b'no-store'),
                    (b'x-content-type-options', b'nosniff'),
                    (b'content-security-policy', b"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'"),
                    (b'referrer-policy', b'no-referrer'),
                    (b'x-frame-options', b'DENY'),
                    (b'permissions-policy', b'camera=(), microphone=(), geolocation=(), display-capture=()'),
                ]
                if self.staging:
                    message['headers'].append((b'strict-transport-security', b'max-age=31536000'))
            await send(message)

        try:
            chunks, length = [], 0
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                chunk = message.get('body', b'')
                chunks.append(chunk)
                length += len(chunk)
                if length > MAX_BODY:
                    await JSONResponse({'error': {'code': 'REQUEST_TOO_LARGE', 'message': 'This request is too large.'}}, status_code=413)(scope, receive, safe_send)
                    return
                if not message.get('more_body', False):
                    break
            pending = True

            async def bounded_receive():
                nonlocal pending
                if pending:
                    pending = False
                    return {'type': 'http.request', 'body': b''.join(chunks), 'more_body': False}
                return await receive()

            await self.app(scope, bounded_receive, safe_send)
        except Exception:
            if not started:
                await JSONResponse({'error': {'code': 'INTERNAL_ERROR', 'message': 'The management service is unavailable.'}}, status_code=500)(scope, receive, safe_send)


def create_app(settings: CloudSettings | None = None, probe: ReadinessProbe | None = None, web_dist: Path | None = None) -> FastAPI:
    from .control_auth import create_auth_router
    from .control_operations import create_device_router, create_operations_router
    from .control_store import ControlError, ControlStore

    settings = settings or CloudSettings.from_env()
    readiness = probe or ReadinessProbe(settings)
    app = FastAPI(title='AisleSignals management', docs_url=None, redoc_url=None,
                  openapi_url=None, debug=False, redirect_slashes=False)
    store = ControlStore(settings)
    app.state.control_store = store

    @app.exception_handler(ControlError)
    async def control_error(request, error):
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=error.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, error):
        # Validation errors must not echo passwords, tokens or submitted notes.
        return JSONResponse({'error': {'code': 'INVALID_INPUT', 'message': 'Check the required fields and try again.'}}, status_code=422)

    app.include_router(create_auth_router(settings, store))
    app.include_router(create_operations_router())
    app.include_router(create_device_router())

    @app.get('/health/live')
    async def live():
        return {'status': 'alive', 'stage': 'management-console'}

    @app.get('/health/ready')
    async def ready():
        result = await readiness.check()
        return JSONResponse(
            {'status': 'ready' if result.ready else 'unavailable', 'code': result.code, 'scope': 'database-schema-only'},
            status_code=200 if result.ready else 503,
            headers={} if result.ready else {'Retry-After': '2'},
        )

    directory = web_dist if web_dist is not None else WEB_DIST

    @app.get('/')
    async def index():
        entry = directory / 'index.html'
        if not entry.is_file():
            return JSONResponse({'error': {'code': 'INTERFACE_NOT_BUILT', 'message': 'The management interface is being prepared.'}}, status_code=503)
        return FileResponse(entry, media_type='text/html')

    @app.get('/downloads/cloud_companion.py')
    async def companion():
        return FileResponse(Path(__file__).resolve().parents[2] / 'scripts' / 'cloud_companion.py',
                            media_type='text/plain', filename='cloud_companion.py')

    @app.get('/downloads/cloud-companion.zip')
    async def companion_package():
        # An explicit source-file allowlist; never package the scripts directory
        # or any runtime files. The Windows adapter travels with its caller.
        content = io.BytesIO()
        scripts = Path(__file__).resolve().parents[2] / 'scripts'
        with zipfile.ZipFile(content, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name in ('cloud_companion.py', 'cloud_private_windows.py'):
                entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, (scripts / name).read_bytes())
        return Response(content.getvalue(), media_type='application/zip',
                        headers={'Content-Disposition': 'attachment; filename="AisleSignals-connection-tool.zip"'})

    app.mount('/assets', StaticFiles(directory=directory / 'assets', check_dir=False), name='control-assets')
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts), www_redirect=False)
    app.add_middleware(SafeResponses, staging=settings.environment == 'staging')
    return app
