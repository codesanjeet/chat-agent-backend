from pydantic import BaseModel, field_validator
import uuid

class CompleteProfileRequest(BaseModel):
    business_name: str
    business_category: str
    slug: str | None = None  

    @field_validator("business_name", "business_category")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("This field cannot be empty")
        return v