from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import Cache, get_cache
from app.config import Settings, get_settings
from app.db.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CacheDep = Annotated[Cache, Depends(get_cache)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def require_api_key(x_api_key: Annotated[str | None, Header()] = None, settings: Settings = Depends(get_settings)) -> None:
    if not x_api_key or x_api_key != settings.api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
