from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import User

_JWT_ALGORITHM = "HS256"
_JWT_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days


def _jwt_secret() -> str:
    return os.getenv("JWT_SECRET_KEY", "")


def create_access_token(user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=_JWT_EXPIRE_MINUTES)
    return jwt.encode({"sub": user_id, "exp": expire}, _jwt_secret(), algorithm=_JWT_ALGORITHM)


def decode_access_token_raw(token: str) -> Optional[str]:
    """Decode a JWT token and return the user_id (sub claim), or None if invalid.

    Used in WebSocket handlers where FastAPI's Depends() is not available.
    """
    secret = _jwt_secret()
    if not secret:
        return None
    try:
        payload = jwt.decode(token, secret, algorithms=[_JWT_ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


async def get_current_user(
    access_token: Optional[str] = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )
    if not access_token:
        raise credentials_exception

    user_id = decode_access_token_raw(access_token)
    if not user_id:
        raise credentials_exception

    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    return user
