from fastapi import APIRouter

from app.api.healthcheck import router as healthcheck_router

router = APIRouter()
router.include_router(healthcheck_router)
