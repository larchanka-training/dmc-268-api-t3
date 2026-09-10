from fastapi import APIRouter

router = APIRouter()


@router.get("/healthcheck")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
