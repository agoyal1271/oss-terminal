from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router

app = FastAPI(
    title="OSS Terminal API",
    description="Free, open-data equity research API built on SEC EDGAR/XBRL and public price data.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    # This API is public, read-only, and sends no cookies/credentials --
    # anyone can already read the same data straight from SEC EDGAR and
    # Yahoo, so an open origin list has no meaningful downside here.
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}
