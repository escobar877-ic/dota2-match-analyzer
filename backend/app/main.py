from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.matches import router as matches_router
from app.api.betting import router as betting_router
from app.api.data_sync import router as data_sync_router
from app.api.heroes import router as heroes_router
from app.api.models import router as models_router
from app.api.patches import router as patches_router
from app.api.players import router as players_router
from app.api.rosters import router as rosters_router
from app.api.teams import router as teams_router
from app.api.tier1 import router as tier1_router
from app.config import settings
from app.database import get_db
from app.health import build_system_readiness


app = FastAPI(
    title="Dota 2 Match Analyzer API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(teams_router)
app.include_router(heroes_router)
app.include_router(players_router)
app.include_router(matches_router)
app.include_router(betting_router)
app.include_router(tier1_router)
app.include_router(models_router)
app.include_router(data_sync_router)
app.include_router(rosters_router)
app.include_router(patches_router)


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(_request, _exc: SQLAlchemyError) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"detail": "Database error"},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "dota-analyzer-backend",
    }


@app.get("/health/ready")
def readiness(db: Session = Depends(get_db)) -> dict:
    return build_system_readiness(db)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "dota-analyzer-backend",
        "health": "/health",
        "database_url_configured": str(bool(settings.database_url)),
    }
