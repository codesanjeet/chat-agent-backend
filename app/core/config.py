from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

class Settings(BaseSettings):

    
    # Security
    SECRET_KEY: str = "ioWsj9QyrDlEQEDVDsQJM8UdTBcEnSsE6ZblFRGejR8Z9aHYXH6yiWc5dXaaxmpLMtyjOP6oA_pPBMK257olyq"  # openssl rand -hex 32
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    EMBEDDING_DIM: int = 1536

    GOOGLE_CLIENT_ID: str
    OPENAI_API_KEY: str
    GEMINI_MODEL: str = "gemini-2.5-flash"
    EMBEDDING_MODEL : str = "text-embedding-3-small"
    MAX_UPLOAD_MB : int = 5
    
    # CORS
    BACKEND_CORS_ORIGINS: List[str] = [
        "https://agent.kiteum.com",
        "https://api.agent.kiteum.com",
    ]
    
    # # Database
    # DATABASE_URL: str
    
    # # Email (Resend)s
    RESEND_API_KEY: str
    EMAIL_FROM_ADDRESS: str = "no-reply@kiteum.com"
    
    # Frontend
    # FRONTEND_URL: str = "http://localhost:3000"
    FRONTEND_URL: str = "https://agent.kiteum.com"
    
    # Environment
    ENVIRONMENT: str = "development"
    
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )
settings = Settings()
