from fastapi import APIRouter

from app.api.healthcheck import router as healthcheck_router
from app.api.webhooks import router as webhooks_router

router = APIRouter()
router.include_router(healthcheck_router)
router.include_router(webhooks_router)
