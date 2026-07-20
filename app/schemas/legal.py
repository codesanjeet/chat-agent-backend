from datetime import datetime

from pydantic import BaseModel


class AgreementAcceptIn(BaseModel):
    document_type: str      # "tos" | "privacy"
    document_version: str   # "v1"


class AgreementOut(BaseModel):
    document_type: str
    document_version: str
    accepted_at: datetime

    class Config:
        from_attributes = True


# Needed only once you have multiple versions and need to detect users who
# haven't accepted the latest one (e.g. re-prompt after a ToS update).
#
# class AgreementStatusOut(BaseModel):
#     document_type: str
#     current_version: str
#     accepted: bool
#     accepted_version: str | None = None-