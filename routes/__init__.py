"""Route registration — imports and registers all route modules.

Usage in main.py:
    from routes import register_routes
    register_routes(app)
"""
from fastapi import FastAPI


def register_routes(app: FastAPI) -> None:
    """Register all API route modules with the FastAPI app."""
    from routes.braindump import router as braindump_router
    from routes.schedule import router as schedule_router
    from routes.memory import router as memory_router
    from routes.system import router as system_router
    from routes.security import router as security_router
    from routes.integrations import router as integrations_router
    from routes.agents import router as agents_router
    from routes.sync import router as sync_router

    app.include_router(braindump_router)
    app.include_router(schedule_router)
    app.include_router(memory_router)
    app.include_router(system_router)
    app.include_router(security_router)
    app.include_router(integrations_router)
    app.include_router(agents_router)
    app.include_router(sync_router)
