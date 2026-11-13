"""Set up an empty database for a hosted deployment.

Runs every db/*.sql file in order and creates the demo logins, but only when the
database has no tables yet, so it is safe to run on every start:

    python -m app.init_db
"""
from pathlib import Path

from sqlalchemy import text

from app.database import engine

DB_DIR = Path(__file__).resolve().parent.parent / "db"


def is_empty() -> bool:
    with engine.connect() as conn:
        return conn.execute(text("SELECT to_regclass('public.patient') IS NULL")).scalar()


def run_sql_files() -> None:
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        for f in sorted(DB_DIR.glob("*.sql")):
            print("running", f.name)
            cur.execute(f.read_text(encoding="utf-8"))   # no parameters: several statements are allowed
        raw.commit()
    finally:
        raw.close()


if __name__ == "__main__":
    if is_empty():
        run_sql_files()
        from app.seed_users import DEV_PASSWORD, DEV_USERS
        from app.auth import create_user
        for username, role, staff_id, patient_id in DEV_USERS:
            create_user(username, DEV_PASSWORD, role, staff_id, patient_id)
        print(f"Database ready, {len(DEV_USERS)} demo logins created.")
    else:
        print("Database already set up, nothing to do.")
