"""
Unit tests for `image_service.load_image` on malformed files (T8 §4).

A malformed GIF can make Pillow's frame count raise. That must be the normal 422
"invalid image", not a 500.

Run with:
    PYTHONPATH=backend python -m pytest tests/unit/test_load_image.py -q
"""
from __future__ import annotations

import io

import pytest
from PIL import Image as PILImage

from app.common.exceptions import BuzzerError
from app.services.image_service import load_image


def _gif(frames: int = 1) -> bytes:
    buf = io.BytesIO()
    colors = [(255, 0, 0), (0, 0, 255), (0, 255, 0)]
    first, *rest = [PILImage.new("RGB", (4, 4), colors[n]) for n in range(frames)]
    first.save(buf, "GIF", save_all=bool(rest), append_images=rest)
    return buf.getvalue()


def test_still_gif_loads():
    loaded = load_image(_gif())
    assert (loaded.content_type, loaded.width, loaded.height) == ("image/gif", 4, 4)


def test_animated_gif_is_422():
    with pytest.raises(BuzzerError) as exc:
        load_image(_gif(frames=2))
    assert exc.value.status_code == 422
    assert "Animated" in exc.value.message


@pytest.mark.parametrize(
    "tail",
    [
        b",",  # image descriptor cut off
        b",\x00\x00",  # image descriptor too short
        b"!\xf9\x04",  # extension block cut off (IndexError in Pillow)
    ],
)
def test_malformed_gif_frames_are_422(tail: bytes):
    # Replace the GIF trailer (";") with a broken second block.
    data = _gif()[:-1] + tail
    with pytest.raises(BuzzerError) as exc:
        load_image(data)
    assert exc.value.status_code == 422
