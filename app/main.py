from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.db import init_db
from app.deps import close_all
from app.routers import bootstrap, kitchen, pantry, prices, transactions


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield
    await close_all()


app = FastAPI(
    title="Masroufi",
    description=(
        "Expense tracking built around a bank statement export and its note "
        "field. Phase A: import, categorise, and surface repeat patterns for the "
        "household to confirm."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(transactions.router, prefix="/api")
app.include_router(kitchen.router, prefix="/api/kitchen", tags=["kitchen"])
app.include_router(pantry.router, prefix="/api/pantry", tags=["pantry"])
app.include_router(prices.router, prefix="/api/prices", tags=["prices"])
app.include_router(bootstrap.router, prefix="/api/bootstrap", tags=["bootstrap"])


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "classifier": settings.classifier_backend}
