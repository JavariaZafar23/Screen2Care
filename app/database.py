import os

from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://loop:loop_dev_password@localhost:5432/screening_care_loop",
)
# Hosted databases (Neon, Render, ...) hand out postgres:// or postgresql:// URLs;
# SQLAlchemy needs the driver name to use psycopg 3.
for prefix in ("postgres://", "postgresql://"):
    if DATABASE_URL.startswith(prefix):
        DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len(prefix):]
        break

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
