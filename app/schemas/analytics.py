from pydantic import BaseModel


class ConversationDayOut(BaseModel):
    day: str            # "Mon", "Tue", ... -- matches conversationData shape in DashboardView
    conversations: int


class LeadDayOut(BaseModel):
    day: str
    leads: int


class ResolutionSliceOut(BaseModel):
    name: str            # "Answered by bot" | "Escalated to admin"
    value: int
    color: str           # kept server-side so the frontend never hardcodes brand colors


class AnalyticsOut(BaseModel):
    """Everything DashboardView needs, one call. All three lists are derived
    from distinct chat *sessions*, not raw message rows -- a visitor sending
    5 messages in one session is 1 conversation, and a visitor who returns
    days later on the same session_id is never re-counted on the new day.
    See crud/analytics.py for how that's enforced at the query level.
    """

    conversations: list[ConversationDayOut]
    leads: list[LeadDayOut]
    resolution: list[ResolutionSliceOut]