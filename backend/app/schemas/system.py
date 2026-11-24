from typing import Literal

from pydantic import BaseModel


class SystemStatusResponse(BaseModel):
    version: str
    environment: Literal["development", "production"]
    database: Literal["healthy", "unhealthy"]
    ai_provider: Literal["configured", "not_configured"]
    browser_automation: Literal["available", "unavailable"]
