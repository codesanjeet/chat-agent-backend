from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
import os
from urllib.parse import quote_plus
from sqlalchemy import text
import asyncio
from sqlalchemy.pool import NullPool

# Load environment variables from .env
load_dotenv()

# Get the database URL from environment variables
# DB_USER = os.getenv("DB_USER")
# DB_PASSWORD = os.getenv("DB_PASSWORD")
# DB_HOST = os.getenv("DB_HOST")
# DB_PORT = os.getenv("DB_PORT", 5432) 
# DB_NAME = os.getenv("DB_NAME")


# Encode the password for the URL
# encoded_password = quote_plus(DB_PASSWORD)

# DATABASE = f'postgresql+asyncpg://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}'
# for render
DATABASE = os.getenv("DATABASE_URL")

# Create an async engine and session
engine = create_async_engine(
    DATABASE,
    connect_args={
        "statement_cache_size": 0
    },
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=5,        
    max_overflow=15,    
)
# engine = create_async_engine(DATABASE, echo=True)
AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Dependency to get the DB session (for FastAPI usage, not needed for this test)
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# Function to test database connection
async def test_connection():
    try:
        async with engine.begin() as conn:
            # Test the connection by executing a simple query
            result = await conn.execute(text("SELECT 1"))
            if result.scalar() == 1:
                print("Database connected successfully!")
            else:
                print("Database connection failed!")
    except Exception as e:
        print(f"Error while connecting to the database: {e}")

# Run the test connection function only if this is the main program
if __name__ == "__main__":
    asyncio.run(test_connection())