"""T8 image limits and shapes (docs/plans/t8-images.md §4, §6). One place for every cap, shared
by upload, import, export and the request-size middleware."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

MiB = 1_048_576

MAX_IMAGE_BYTES = 5 * MiB  # one image file
MAX_IMAGE_SIDE = 4096  # px, width and height
MAX_IMAGE_PIXELS = MAX_IMAGE_SIDE * MAX_IMAGE_SIDE  # 16,777,216; Pillow's guard
MAX_IMPORT_BYTES = 30 * MiB  # one quiz import/export file
MAX_BUNDLE_IMAGES = 20  # images per v2 import/export
MAX_BUNDLE_IMAGE_BYTES = 20 * MiB  # decoded image bytes per v2 import/export
MAX_COURSE_IMAGES = 200  # current images per course
MAX_COURSE_IMAGE_BYTES = 500 * MiB  # bytes of current images per course
MAX_REQUEST_BYTES = 32 * MiB  # any request body (middleware; matches nginx 32m)

# Decoder format -> stored content type. MPO is a phone-camera JPEG with an embedded image.
CONTENT_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "MPO": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


def image_url(public_key: str) -> str:
    return f"/api/images/{public_key}"


class ImageItem(BaseModel):
    id: int
    course_id: int
    filename: str
    content_type: str
    size_bytes: int
    width: int
    height: int
    url: str
    created_at: datetime
    # Questions referencing it in non-archived / archived quizzes; delete needs both 0.
    use_count: int
    archived_use_count: int
