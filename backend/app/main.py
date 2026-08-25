"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .api.team import router as team_router
from .config import settings

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    settings.sessions_dir.mkdir(parents=True, exist_ok=True)
    settings.checkpoints_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    lifespan=lifespan,
    title="KiCAD Mitos",
    version="0.1.0",
    description="Natural-language batch editing for KiCAD, verified with KiCAD's own ERC.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(team_router)
