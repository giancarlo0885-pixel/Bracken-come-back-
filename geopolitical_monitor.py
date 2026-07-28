from __future__ import annotations
from dataclasses import dataclass
from api_manager import get_api_settings

@dataclass
class ProviderResult:
    available: bool
    provider: str
    records: list[dict]
    message: str

def fetch() -> ProviderResult:
    settings=get_api_settings()
    configured=any(settings.has(name) for name in {'NEWS_API_KEY'})
    if not configured:
        return ProviderResult(False,"Not configured",[],"Add a supported provider key later. No data is fabricated.")
    return ProviderResult(True,"Configured provider",[],"Provider adapter is ready for implementation.")
