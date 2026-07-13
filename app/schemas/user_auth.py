import uuid

from pydantic import BaseModel, EmailStr


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    business_name: str | None = None
    business_category: str | None = None
    slug: str | None
    logo: str | None = None
    profile_completed: bool
    is_configured: bool

    class Config:
        from_attributes = True


class GoogleLoginRequest(BaseModel):
    id_token: str


class OtpRequest(BaseModel):
    email: EmailStr


class OtpVerify(BaseModel):
    email: EmailStr
    code: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut