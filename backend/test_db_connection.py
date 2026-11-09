
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# Load database settings from .env
load_dotenv()

database_url = os.getenv("DATABASE_URL")

if not database_url:
    raise ValueError("DATABASE_URL not found in .env")

# Create database connection
engine = create_engine(database_url)

try:
    with engine.connect() as connection:
        result = connection.execute(
            text("SELECT current_database(), current_user")
        )

        print("Database connection successful!")
        print("Connected database and user:", result.fetchone())

except Exception as error:
    print("Database connection failed!")
    print(type(error).__name__, error)
