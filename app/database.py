from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
# SessionLocal is a session factory (used like a class), following the standard
# SQLAlchemy/FastAPI naming convention, not a constant.
# pylint: disable-next=invalid-name
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
