from threads_poster.db.base import Base
from threads_poster.db.session import engine


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
