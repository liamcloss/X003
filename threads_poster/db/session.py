from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from threads_poster.core.config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
