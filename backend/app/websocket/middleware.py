"""JWT authentication helper for socket connections."""

from __future__ import annotations

from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.user import User
from ..services.auth_service import decode_token, get_user_by_id


async def authenticate_socket(auth: dict | None, db: AsyncSession) -> User:
    """
    Validate the JWT supplied in the socket.io auth dict.
    Raises ValueError with a descriptive message on any failure.
    The caller should return False from the connect handler to reject the connection.
    """
    if not auth:
        raise ValueError("Authentication required")
    token = auth.get("token")
    if not token:
        raise ValueError("Token missing from auth dict")
    try:
        payload = decode_token(token)
    except JWTError:
        raise ValueError("Invalid or expired token")
    if payload.get("token_type") not in ("access", "temp"):
        raise ValueError("Invalid token type")
    user = await get_user_by_id(db, payload["sub"])
    if not user:
        raise ValueError("User not found")
    return user
