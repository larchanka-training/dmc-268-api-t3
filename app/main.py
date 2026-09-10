from fastapi import FastAPI

from app.api import router

app = FastAPI(title="DMC-268 API", version="0.1.0")
app.include_router(router)
