"""Public image view (T8 design §6): anyone holding the unguessable URL can view the image.

Every version has its own URL and its bytes never change, so responses are cached forever.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from ..database import DbSession
from ..services import image_service

router = APIRouter(prefix="/images", tags=["images"])


@router.get("/{public_key}")
async def view_image(public_key: str, db: DbSession) -> Response:
    image = await image_service.get_public(db, public_key)
    return Response(
        content=image.data,
        media_type=image.content_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )
