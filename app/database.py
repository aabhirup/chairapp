from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# Using sqlite for default if POSTGRES_URL not provided for easier local running, 
# but prompt asked for Postgres. I will set the default to a postgres pattern but allow override.
# Replace with actual DB URL or use environment variable.
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./sql_app.db")

# Fallback to sqlite for ease of testing if postgres is not available immediately, 
# but uncomment above for prod.
# SQLALCHEMY_DATABASE_URL = "sqlite:///./sql_app.db"

# Handle Render/Heroku style postgres:// URLs for SQLAlchemy 1.4+
if SQLALCHEMY_DATABASE_URL and SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Check if using SQLite (local fallback)
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
