import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .core.database import engine
from .routers import user_auth as auth_router
from .routers import document as document_router
from .routers import gallery as gallery_router
from .routers import user as user_router
from .routers import lead as lead_router
from .routers import chat as chat_router
from .routers import setttings as setting_router

logger = logging.getLogger(__name__)

CORS_ORIGINS = [
    "https://agent.kiteum.com",
    "http://localhost:3000",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Kiteum Agent API...")
    yield
    # Shutdown
    logger.info("Shutting down Kiteum Agent API...")
    await engine.dispose()


app = FastAPI(
    title="Kiteum Agent API",
    version="1.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Tenant-Slug"],
)

# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled exception on %s: %s", request.url.path, exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(auth_router.router)
app.include_router(document_router.router)
app.include_router(gallery_router.router)
app.include_router(user_router.router)
app.include_router(lead_router.router)
app.include_router(chat_router.router)
app.include_router(setting_router.router)



@app.get("/health")
async def health_check():
    return {"status": "ok"}