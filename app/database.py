import os

from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://loop:loop_dev_password@localhost:5432/screening_care_loop",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
