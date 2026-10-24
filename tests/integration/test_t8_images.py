"""
T8 integration tests: course images (upload, view, replace, delete), question references,
export/import version 2, live game payloads and the locking paths
(docs/plans/t8-images.md §12).

Runs against the live Docker stack like the rest of the suite and reuses the T4 fixture
(`t4`: two fresh `Test Course <hex>` courses with the shared users). Quizzes are removed by
the T4 teardown; the `t8` fixture then removes every image of those two courses with
`scripts.cleanup_test_quiz --image-id` (the courses are created by the fixture, so that is
exactly what the test made — no shared course is touched).
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import pathlib
import socket
import uuid

import httpx
import pytest
from PIL import Image

from .conftest import _REPO_ROOT, create_guest_tokens, run_backend_script
from .engine.socket_client import TestSocketClient as SocketClient
from .test_t4_host_admin import (  # noqa: F401  (t4 is a fixture)
    EV,
    GameRowLock,
    H,
    T,
    end,
    join,
    mk_quiz,
    sql,
    start_room,
    t4,
)

MiB = 1_048_576


# ---------------------------------------------------------------------------
# Fixture and helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def t8(t4):  # noqa: F811
    yield t4
    ids = sql(f"SELECT id FROM images WHERE course_id IN ({t4['c1']}, {t4['c2']})").split()
    if ids:
        args = [a for i in ids for a in ("--image-id", i)]
        result = run_backend_script("scripts.cleanup_test_quiz", *args)
        assert result.returncode == 0, result.stdout + result.stderr


def img(fmt: str = "PNG", size=(8, 8), color: str = "red", **kw) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt, **kw)
    return buf.getvalue()


def animated_gif() -> bytes:
    frames = [Image.new("RGB", (4, 4), c) for c in ("red", "blue")]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:])
    return buf.getvalue()


def host_of(t8, course: int) -> str:
    """Host A runs course 1; host C runs course 2."""
    return t8["c"] if course == t8["c2"] else t8["a"]


def upload_raw(t8, data: bytes, name: str = "a.png", course: int | None = None, token=None):
    course = course or t8["c1"]
    return httpx.post(
        f"{t8['base']}/api/host/courses/{course}/images",
        files={"file": (name, data, "application/octet-stream")},
        headers=H(token or host_of(t8, course)),
        timeout=T,
    )


def up(t8, color: str = "red", course: int | None = None, fmt: str = "PNG") -> dict:
    r = upload_raw(t8, img(fmt, color=color), f"{color}.{fmt.lower()}", course)
    assert r.status_code == 201, r.text
    return r.json()


def replace(t8, image_id: int, color: str = "blue", token=None) -> httpx.Response:
    return httpx.put(
        f"{t8['base']}/api/host/images/{image_id}",
        files={"file": (f"{color}.png", img(color=color), "image/png")},
        headers=H(token or t8["a"]),
        timeout=T,
    )


def delete(t8, image_id: int, token=None) -> httpx.Response:
    return httpx.delete(
        f"{t8['base']}/api/host/images/{image_id}", headers=H(token or t8["a"]), timeout=T
    )


def view(t8, url: str) -> httpx.Response:
    return httpx.get(f"{t8['base']}{url}", timeout=T)


def listing(t8, course: int | None = None) -> dict[int, dict]:
    r = httpx.get(
        f"{t8['base']}/api/host/courses/{course or t8['c1']}/images",
        headers=H(t8["a"]),
        timeout=T,
    )
    assert r.status_code == 200, r.text
    return {i["id"]: i for i in r.json()}


def q(qtype: str, config: dict, prompt: str = "Q", grading: str = "COMPLETENESS",
      answer_data: dict | None = None, points: float = 1.0) -> dict:
    return {
        "type": qtype,
        "grading_type": grading,
        "prompt": prompt,
        "config": config,
        "answer_data": answer_data or {},
        "points_value": points,
        "time_limit_seconds": 30,
    }


def mc_img(prompt: str, prompt_image: int | None = None, option_images=None) -> dict:
    """ACCURACY multiple choice where option A is right (play_full answers A)."""
    config: dict = {"options": ["A", "B"]}
    if prompt_image is not None:
        config["promptImageId"] = prompt_image
    if option_images is not None:
        config["optionImageIds"] = option_images
    return q("multiple_choice", config, prompt, "ACCURACY", {"answer_points": [1, 0]})


def new_quiz(t8, token=None, course: int | None = None) -> int:
    gid, _ = mk_quiz(t8, token or t8["a"], course or t8["c1"], prompts=())
    return gid


def add_q(t8, gid: int, body: dict, token=None) -> httpx.Response:
    return httpx.post(
        f"{t8['base']}/api/host/games/{gid}/questions",
        json=body,
        headers=H(token or t8["a"]),
        timeout=T,
    )


def put_q(t8, gid: int, qid: int, body: dict) -> httpx.Response:
    return httpx.put(
        f"{t8['base']}/api/host/games/{gid}/questions/{qid}",
        json=body,
        headers=H(t8["a"]),
        timeout=T,
    )


def questions(t8, gid: int) -> list[dict]:
    r = httpx.get(f"{t8['base']}/api/host/games/{gid}/questions", headers=H(t8["a"]), timeout=T)
    assert r.status_code == 200, r.text
    return r.json()


def archive(t8, gid: int, restore: bool = False) -> None:
    action = "restore" if restore else "archive"
    r = httpx.post(f"{t8['base']}/api/host/games/{gid}/{action}", headers=H(t8["a"]), timeout=T)
    assert r.status_code == 200, r.text


def export(t8, gid: int) -> httpx.Response:
    return httpx.get(f"{t8['base']}/api/host/games/{gid}/export", headers=H(t8["a"]), timeout=T)


def import_file(t8, raw: bytes, course: int | None = None) -> httpx.Response:
    course = course or t8["c1"]
    r = httpx.post(
        f"{t8['base']}/api/host/courses/{course}/games/import",
        files={"file": ("quiz.json", raw, "application/json")},
        headers=H(host_of(t8, course)),
        timeout=30,
    )
    if r.status_code == 201:
        t8["created"].append(r.json()["game_id"])
    return r


async def play_one(t8, gid: int) -> str:
    """Play every question of a quiz once (the player answers A); returns the session id."""
    n = len(questions(t8, gid))
    code, sid = start_room(t8, t8["a"], gid)
    host = await join(t8["base"], t8["a"], code, "HOST", "host")
    player = await join(t8["base"], t8["player"], code, "PLAYER", "player")
    try:
        for _ in range(n):
            await host.emit("host_advance", {})
            nq = await player.wait_for("new_question", EV)
            await player.emit(
                "submit_answer",
                {"question_id": nq["questionId"], "answer_data": {"selectedIndex": 0}},
            )
            await player.wait_for("answer_received", EV)
            await host.emit("host_advance", {})
            await host.wait_for("question_results", EV)
        await host.emit("host_advance", {})
        await host.wait_for("game_over", EV)
    finally:
        await host.disconnect()
        await player.disconnect()
    return sid


# ---------------------------------------------------------------------------
# 1, 5c. Upload and validation
# ---------------------------------------------------------------------------


def test_upload_accepts_still_formats(t8):
    for fmt, ctype in (("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp"),
                       ("GIF", "image/gif")):
        r = upload_raw(t8, img(fmt, size=(30, 20)), f"x.{fmt.lower()}")
        assert r.status_code == 201, (fmt, r.text)
        item = r.json()
        assert (item["content_type"], item["width"], item["height"]) == (ctype, 30, 20)
        assert item["use_count"] == 0 and item["archived_use_count"] == 0
    # The display name loses any folder part.
    r = upload_raw(t8, img(), "C:/photos/cat.png")
    assert r.status_code == 201 and r.json()["filename"] == "cat.png"


@pytest.mark.parametrize(
    "data, status",
    [
        (b"x" * (5 * MiB + 1), 413),  # over the 5 MiB file cap
        (b'<svg xmlns="http://www.w3.org/2000/svg"/>', 415),
        (b"just some text", 415),
        (img("BMP"), 415),  # a real image, not an allowed format
        (img(size=(64, 64))[:60], 422),  # truncated PNG
        (animated_gif(), 422),
        (img(size=(4097, 4)), 422),  # one side over 4096 px
        (img(size=(5000, 100)), 422),  # 5c: refused from the header, small in bytes
        (b"", 422),
    ],
    ids=["too-big", "svg", "text", "bmp", "truncated", "animated", "4097px", "5000x100", "empty"],
)
def test_upload_rejections(t8, data, status):
    r = upload_raw(t8, data)
    assert r.status_code == status, r.text
    assert listing(t8) == {}


def test_request_over_32_mib_is_refused_before_reading(t8):
    """5e: the Content-Length middleware answers 413 {error, message} without the body."""
    url = httpx.URL(t8["base"])
    with socket.create_connection((url.host, url.port or 80), timeout=T) as s:
        s.sendall(
            (
                f"POST /api/host/courses/{t8['c1']}/images HTTP/1.1\r\n"
                f"Host: {url.host}\r\nContent-Type: application/octet-stream\r\n"
                f"Content-Length: {33 * MiB}\r\nConnection: close\r\n\r\n"
            ).encode()
        )
        reply = b""
        while b"}" not in reply:
            chunk = s.recv(4096)
            if not chunk:
                break
            reply += chunk
    head, _, body = reply.partition(b"\r\n\r\n")
    assert head.split()[1] == b"413", reply
    assert json.loads(body)["error"] == "PAYLOAD_TOO_LARGE"


def test_course_storage_cap_counts_current_images_only(t8):
    """5e: 200 current images fill a course; replaced history doesn't count; a replace at the
    count cap still works (the count doesn't change)."""
    seed = up(t8)
    digits = " UNION ALL ".join(f"SELECT {i} AS n" for i in range(10))
    tens = " UNION ALL ".join(f"SELECT {i} AS m" for i in range(20))
    sql(
        "INSERT INTO images (course_id, public_key, filename, content_type, size_bytes, "
        "width, height, data) SELECT i.course_id, UUID(), 'copy.png', i.content_type, "
        f"i.size_bytes, i.width, i.height, i.data FROM images i, ({digits}) d, ({tens}) t "
        f"WHERE i.id = {seed['id']} LIMIT 199"
    )
    assert len(listing(t8)) == 200
    r = upload_raw(t8, img())
    assert r.status_code == 409 and "storage is full" in r.text
    r = replace(t8, seed["id"])
    assert r.status_code == 201, r.text
    # Turn the copies into history: the course has room again.
    sql(
        f"UPDATE images SET replaced_by_id = {r.json()['id']} "
        f"WHERE course_id = {t8['c1']} AND filename = 'copy.png'"
    )
    assert upload_raw(t8, img()).status_code == 201


# ---------------------------------------------------------------------------
# 2. Access and the public view
# ---------------------------------------------------------------------------


def test_access_and_public_view(t8):
    base = t8["base"]
    image = up(t8)
    # Another course's host: 403 on everything.
    assert upload_raw(t8, img(), token=t8["c"]).status_code == 403
    r = httpx.get(f"{base}/api/host/courses/{t8['c1']}/images", headers=H(t8["c"]), timeout=T)
    assert r.status_code == 403
    assert replace(t8, image["id"], token=t8["c"]).status_code == 403
    assert delete(t8, image["id"], token=t8["c"]).status_code == 403
    # A guest: refused on host routes.
    gid = new_quiz(t8)
    assert add_q(t8, gid, q("true_false", {})).status_code == 201
    code, sid = start_room(t8, t8["a"], gid)
    guest = create_guest_tokens(base, code, 1)[0]
    assert upload_raw(t8, img(), token=guest).status_code in (401, 403)
    assert delete(t8, image["id"], token=guest).status_code in (401, 403)
    end(t8, t8["a"], sid)
    # No auth needed to view; headers per §6.
    r = view(t8, image["url"])
    assert r.status_code == 200 and r.content == img()
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-disposition"] == "inline"
    assert view(t8, f"/api/images/{uuid.uuid4()}").status_code == 404


# ---------------------------------------------------------------------------
# 3, 5b, 5d, 5g. References on create and update
# ---------------------------------------------------------------------------


def test_prompt_option_and_pin_references(t8):
    gid = new_quiz(t8)
    a, b = up(t8, "red"), up(t8, "blue")
    bodies = [
        q("multiple_choice", {"options": ["x", "y"], "promptImageId": a["id"],
                              "optionImageIds": [b["id"], None]}),
        q("true_false", {"promptImageId": a["id"]}),
        q("fill_in_the_blank", {"promptImageId": a["id"]}),
        q("multi_select", {"options": ["x", ""], "optionImageIds": [None, b["id"]],
                           "promptImageId": a["id"]}),
        q("pin_drop", {"imageId": b["id"], "promptImageId": a["id"]}),
        q("closest_guess", {"unit": "m", "promptImageId": a["id"]}),
    ]
    for body in bodies:
        r = add_q(t8, gid, body)
        assert r.status_code == 201, (body["type"], r.text)
        assert r.json()["config"] == body["config"]
        assert set(r.json()["images"]) <= {str(a["id"]), str(b["id"])}
    counts = listing(t8)
    assert counts[a["id"]]["use_count"] == 6 and counts[b["id"]]["use_count"] == 3


@pytest.mark.parametrize(
    "body, fragment",
    [
        (q("true_false", {"optionImageIds": [1]}), "only allowed on multiple_choice"),
        (q("multiple_choice", {"options": ["x", "y"], "imageId": 1}), "only allowed on pin_drop"),
        (q("multiple_choice", {"options": ["x", "y"], "optionImageIds": [1]}), "matching options"),
        (q("multiple_choice", {"options": ["", "y"], "optionImageIds": [None, 1]}),
         "Option A needs text or an image"),
        (q("multiple_choice", {"options": ["x", "  "], "optionImageIds": [1, None]}),
         "Option B needs text or an image"),
        (q("true_false", {"promptImageId": True}), "positive integer"),
        (q("true_false", {"promptImageId": 1.0}), "positive integer"),
        (q("true_false", {"promptImageId": 0}), "positive integer"),
        (q("true_false", {"promptImageId": "5"}), "positive integer"),
        (q("pin_drop", {"imageId": -3}), "positive integer"),
        (q("closest_guess", {"unit": "m", "imageId": 1}), "only allowed on pin_drop"),
    ],
)
def test_reference_shape_errors(t8, body, fragment):
    gid = new_quiz(t8)
    r = add_q(t8, gid, body)
    assert r.status_code == 422 and fragment in r.text, r.text


def test_reference_database_errors(t8):
    gid = new_quiz(t8)
    other = up(t8, course=t8["c2"], color="green")
    gone = up(t8)
    assert delete(t8, gone["id"]).status_code == 204
    for image_id in (other["id"], gone["id"], 99_999_999):
        r = add_q(t8, gid, q("true_false", {"promptImageId": image_id}))
        assert r.status_code == 422 and "promptImageId: image not found" in r.text, r.text
    r = add_q(t8, gid, q("pin_drop", {"imageId": other["id"]}))
    assert r.status_code == 422 and r.json()["detail"].startswith("imageId:"), r.text


def test_update_rules_and_normalization(t8):
    gid = new_quiz(t8)
    a, b = up(t8, "red"), up(t8, "blue")
    # 5b: null fields are dropped before storage.
    r = add_q(t8, gid, q("multiple_choice", {"options": ["x", "y"], "promptImageId": None,
                                            "optionImageIds": [None, None]}))
    assert r.status_code == 201 and r.json()["config"] == {"options": ["x", "y"]}
    plain_id = r.json()["id"]
    r = add_q(t8, gid, q("pin_drop", {"imageId": None}))
    assert r.status_code == 201 and r.json()["config"] == {}
    # 5d legacy: no optionImageIds, empty option text still saves as before.
    assert add_q(t8, gid, q("multiple_choice", {"options": ["", "y"]})).status_code == 201

    r = add_q(t8, gid, q("multiple_choice", {"options": ["x", "y"], "optionImageIds": [a["id"], None]}))
    qid = r.json()["id"]
    # Changing the option count without optionImageIds → 422 (length mismatch).
    r = put_q(t8, gid, qid, {"config": {"options": ["x", "y", "z"], "optionImageIds": [a["id"], None]}})
    assert r.status_code == 422 and "matching options" in r.text, r.text
    # Type change leaving optionImageIds behind → 422.
    r = put_q(t8, gid, qid, {"type": "true_false"})
    assert r.status_code == 422 and "optionImageIds" in r.text, r.text
    # Adding a prompt image and moving the option image: OK.
    r = put_q(t8, gid, qid, {"config": {"options": ["x", "y"], "optionImageIds": [None, a["id"]],
                                        "promptImageId": b["id"]}})
    assert r.status_code == 200, r.text
    # Removing every image: OK, and the fields are gone.
    r = put_q(t8, gid, qid, {"config": {"options": ["x", "y"]}})
    assert r.status_code == 200 and r.json()["config"] == {"options": ["x", "y"]}
    # Regression: a legacy partial update without image fields behaves as before.
    r = put_q(t8, gid, plain_id, {"prompt": "edited"})
    assert r.status_code == 200 and r.json()["prompt"] == "edited"
    # A quiz whose image fields were all null exports as version 1.
    plain = new_quiz(t8)
    add_q(t8, plain, q("true_false", {"promptImageId": None}))
    assert json.loads(export(t8, plain).content)["version"] == 1


# ---------------------------------------------------------------------------
# 4. Replace
# ---------------------------------------------------------------------------


async def test_replace_repoints_current_quizzes_and_keeps_history(t8):
    base = t8["base"]
    first = up(t8, "red")
    old_bytes = view(t8, first["url"]).content
    live = new_quiz(t8)
    assert add_q(t8, live, mc_img("Live", prompt_image=first["id"])).status_code == 201
    kept = new_quiz(t8)
    assert add_q(t8, kept, mc_img("Kept", prompt_image=first["id"])).status_code == 201
    archive(t8, kept)
    sid = await play_one(t8, live)

    r = replace(t8, first["id"], "blue")
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["id"] != first["id"] and new["url"] != first["url"]
    assert new["filename"] == "blue.png"
    # Current quiz re-pointed; archived quiz keeps the old id; old URL serves old bytes.
    assert questions(t8, live)[0]["config"]["promptImageId"] == new["id"]
    assert questions(t8, kept)[0]["config"]["promptImageId"] == first["id"]
    assert view(t8, first["url"]).content == old_bytes
    assert first["id"] not in listing(t8) and new["id"] in listing(t8)
    # The finished session still shows the original image.
    detail = httpx.get(f"{base}/api/player/past-quizzes/{sid}", headers=H(t8["player"])).json()
    assert detail["questions"][0]["config"]["promptImageId"] == first["id"]
    assert detail["image_urls"] == {str(first["id"]): first["url"]}
    report = httpx.get(f"{base}/api/host/sessions/{sid}/report", headers=H(t8["a"])).text
    assert base64.b64encode(old_bytes).decode() in report
    # A second replace of the old version → 409.
    r = replace(t8, first["id"], "green")
    assert r.status_code == 409 and "replaced or deleted" in r.text
    # An open room on a quiz that uses it → 409 naming the quiz.
    code, room_sid = start_room(t8, t8["a"], live)
    try:
        r = replace(t8, new["id"], "green")
        assert r.status_code == 409 and "A room is open for" in r.text, r.text
    finally:
        end(t8, t8["a"], room_sid)
    assert replace(t8, new["id"], "green").status_code == 201


def test_replace_of_unused_image_prunes_old_row(t8):
    first = up(t8)
    r = replace(t8, first["id"])
    assert r.status_code == 201
    assert view(t8, first["url"]).status_code == 404
    assert sql(f"SELECT COUNT(*) FROM images WHERE id = {first['id']}") == "0"


def test_restored_quiz_older_version_and_prune(t8):
    """5f + prune: an archived quiz keeps an older version; after restore the editor marks
    it current=False, an unchanged reference still saves, and switching to the current
    version prunes the older row once nothing uses it."""
    older = up(t8, "red")
    gid = new_quiz(t8)
    qid = add_q(t8, gid, mc_img("P", prompt_image=older["id"])).json()["id"]
    archive(t8, gid)
    current = replace(t8, older["id"]).json()
    archive(t8, gid, restore=True)
    item = questions(t8, gid)[0]
    assert item["images"][str(older["id"])] == {"url": older["url"], "current": False}
    # Unchanged reference to the older version: edits still save.
    assert put_q(t8, gid, qid, {"prompt": "edited"}).status_code == 200
    r = put_q(t8, gid, qid, {"config": {"options": ["A", "B"], "promptImageId": older["id"]}})
    assert r.status_code == 200, r.text
    # A *new* reference to the older version is refused.
    other = add_q(t8, gid, mc_img("Q", prompt_image=older["id"]))
    assert other.status_code == 422 and "image is not current" in other.text
    # Switch to the current version: the older row is pruned.
    r = put_q(t8, gid, qid, {"config": {"options": ["A", "B"], "promptImageId": current["id"]}})
    assert r.status_code == 200 and r.json()["images"][str(current["id"])]["current"] is True
    assert view(t8, older["url"]).status_code == 404


# ---------------------------------------------------------------------------
# 5. Delete, soft delete and the version chain
# ---------------------------------------------------------------------------


def test_delete_refused_while_any_quiz_uses_it(t8):
    image = up(t8)
    gid = new_quiz(t8)
    qid = add_q(t8, gid, mc_img("D", prompt_image=image["id"])).json()["id"]
    archive(t8, gid)
    assert listing(t8)[image["id"]]["archived_use_count"] == 1
    r = delete(t8, image["id"])
    assert r.status_code == 409 and "restore the quiz" in r.text, r.text
    archive(t8, gid, restore=True)
    assert listing(t8)[image["id"]]["use_count"] == 1
    assert delete(t8, image["id"]).status_code == 409
    assert put_q(t8, gid, qid, {"config": {"options": ["A", "B"]}}).status_code == 200
    assert delete(t8, image["id"]).status_code == 204
    assert view(t8, image["url"]).status_code == 404  # hard delete: no snapshot used it


async def test_soft_delete_keeps_history_and_version_chain(t8):
    base = t8["base"]
    a = up(t8, "red")
    gid = new_quiz(t8)
    qid = add_q(t8, gid, mc_img("S", prompt_image=a["id"])).json()["id"]
    sid = await play_one(t8, gid)  # the snapshot now references A
    # Replace A → B (A stays: the snapshot uses it), then remove B from the quiz and delete it.
    b = replace(t8, a["id"]).json()
    assert put_q(t8, gid, qid, {"config": {"options": ["A", "B"]}}).status_code == 200
    assert delete(t8, b["id"]).status_code == 204
    assert view(t8, b["url"]).status_code == 404  # B: hard delete
    # A is still not current (can't be referenced again) but still serves its URL.
    r = put_q(t8, gid, qid, {"config": {"options": ["A", "B"], "promptImageId": a["id"]}})
    assert r.status_code == 422 and "not current" in r.text, r.text
    assert view(t8, a["url"]).status_code == 200
    detail = httpx.get(f"{base}/api/player/past-quizzes/{sid}", headers=H(t8["player"])).json()
    assert detail["image_urls"] == {str(a["id"]): a["url"]}

    # Soft delete: a current image used only by a snapshot.
    c = up(t8, "green")
    gid2 = new_quiz(t8)
    qid2 = add_q(t8, gid2, mc_img("C", prompt_image=c["id"])).json()["id"]
    await play_one(t8, gid2)
    assert put_q(t8, gid2, qid2, {"config": {"options": ["A", "B"]}}).status_code == 200
    assert delete(t8, c["id"]).status_code == 204
    assert view(t8, c["url"]).status_code == 200  # history still renders
    assert c["id"] not in listing(t8)
    r = add_q(t8, gid2, mc_img("again", prompt_image=c["id"]))
    assert r.status_code == 422 and "image deleted" in r.text, r.text


# ---------------------------------------------------------------------------
# 6. Export and import
# ---------------------------------------------------------------------------


def test_export_import_round_trip(t8):
    a, b = up(t8, "red"), up(t8, "blue")
    gid = new_quiz(t8)
    add_q(t8, gid, mc_img("one", prompt_image=a["id"], option_images=[b["id"], None]))
    add_q(t8, gid, q("pin_drop", {"imageId": a["id"]}, "two"))
    raw = export(t8, gid)
    assert raw.status_code == 200, raw.text
    bundle = json.loads(raw.content)
    assert bundle["version"] == 2
    assert [i["key"] for i in bundle["images"]] == ["img-1", "img-2"]  # A used twice: one entry
    assert bundle["questions"][0]["config"]["promptImageId"] == "img-1"
    assert bundle["questions"][1]["config"]["imageId"] == "img-1"
    # Into another course: new image rows there, same bytes.
    r = httpx.post(
        f"{t8['base']}/api/admin/games/import",
        files={"file": ("q.json", raw.content, "application/json")},
        data={"course_id": str(t8["c2"])},
        headers=H(t8["admin"]),
        timeout=30,
    )
    assert r.status_code == 201, r.text
    t8["created"].append(r.json()["game_id"])
    new_qs = httpx.get(
        f"{t8['base']}/api/host/games/{r.json()['game_id']}/questions",
        headers=H(t8["admin"]),
        timeout=T,
    ).json()
    new_a = new_qs[0]["config"]["promptImageId"]
    assert new_a not in (a["id"], b["id"]) and new_qs[1]["config"]["imageId"] == new_a
    assert view(t8, new_qs[0]["images"][str(new_a)]["url"]).content == view(t8, a["url"]).content
    assert sql(f"SELECT COUNT(*) FROM images WHERE course_id = {t8['c2']}") == "2"
    # A quiz without images: version 1, no images key.
    plain = new_quiz(t8)
    add_q(t8, plain, q("true_false", {}))
    assert "images" not in json.loads(export(t8, plain).content)


def test_import_failures_create_nothing(t8):
    a = up(t8)
    gid = new_quiz(t8)
    add_q(t8, gid, mc_img("one", prompt_image=a["id"]))
    good = json.loads(export(t8, gid).content)
    before = sql(f"SELECT (SELECT COUNT(*) FROM games WHERE course_id={t8['c2']}), "
                 f"(SELECT COUNT(*) FROM images WHERE course_id={t8['c2']})")

    def broken(change) -> bytes:
        bundle = json.loads(json.dumps(good))
        change(bundle)
        return json.dumps(bundle).encode()

    cases = {
        "bad base64": broken(lambda b: b["images"][0].update(data="@@@")),
        "unknown key": broken(lambda b: b["questions"][0]["config"].update(promptImageId="img-9")),
        "duplicate key": broken(lambda b: b["images"].append(dict(b["images"][0]))),
        "image field in v1": broken(lambda b: (b.update(version=1), b.pop("images"),
                                               b["questions"][0]["config"].update(promptImageId=a["id"]))),
        "animated entry": broken(lambda b: b["images"][0].update(
            data=base64.b64encode(animated_gif()).decode())),
        "bad later question": broken(lambda b: b["questions"].append(q("true_false", {"imageId": "img-1"}))),
    }
    for name, raw in cases.items():
        r = import_file(t8, raw, course=t8["c2"])
        assert r.status_code == 422, (name, r.status_code, r.text)
    too_many = broken(lambda b: b.update(images=[dict(b["images"][0], key=f"k{i}") for i in range(21)]))
    assert import_file(t8, too_many, course=t8["c2"]).status_code == 413
    after = sql(f"SELECT (SELECT COUNT(*) FROM games WHERE course_id={t8['c2']}), "
                f"(SELECT COUNT(*) FROM images WHERE course_id={t8['c2']})")
    assert after == before


def test_invalid_legacy_question_blocks_export_until_fixed(t8):
    gid = new_quiz(t8)
    qid = add_q(t8, gid, q("true_false", {}, "legacy")).json()["id"]
    sql(f"UPDATE questions SET config = JSON_OBJECT('promptImageId', 'oops') WHERE id = {qid}")
    r = export(t8, gid)
    assert r.status_code == 422 and "Question 1 (true_false)" in r.text, r.text
    assert put_q(t8, gid, qid, q("true_false", {}, "legacy")).status_code == 200
    assert export(t8, gid).status_code == 200


def test_every_sample_game_still_imports(t8):
    for path in sorted(pathlib.Path(_REPO_ROOT, "sample_games").glob("*.json")):
        r = import_file(t8, path.read_bytes())
        assert r.status_code == 201, (path.name, r.text)


# ---------------------------------------------------------------------------
# 7. Live game payloads
# ---------------------------------------------------------------------------


async def test_live_game_carries_image_urls(t8):
    base, a_tok = t8["base"], t8["a"]
    p, o, bg = up(t8, "red"), up(t8, "blue"), up(t8, "green")
    gid = new_quiz(t8)
    add_q(t8, gid, mc_img("mc", prompt_image=p["id"], option_images=[o["id"], None]))
    add_q(t8, gid, q("pin_drop", {"imageId": bg["id"]}, "pin", "ACCURACY",
                     {"target": {"x": 1, "y": -2}}, points=3))
    want_mc = {str(p["id"]): p["url"], str(o["id"]): o["url"]}
    code, sid = start_room(t8, a_tok, gid)
    host = await join(base, a_tok, code, "HOST", "host")
    player = await join(base, t8["player"], code, "PLAYER", "player")
    try:
        await host.emit("host_advance", {})
        nq = await player.wait_for("new_question", EV)
        assert nq["imageUrls"] == want_mc and nq["config"]["optionImageIds"] == [o["id"], None]
        await player.emit("submit_answer", {"question_id": nq["questionId"],
                                            "answer_data": {"selectedIndex": 0}})
        await player.wait_for("answer_received", EV)
        await host.emit("host_advance", {})
        res = await host.wait_for("question_results", EV)
        assert res["imageUrls"] == want_mc
        pres = await player.wait_for("question_results", EV)
        assert pres["imageUrls"] == want_mc
        # Pin Drop with a background scores exactly as without one.
        await host.emit("host_advance", {})
        pin = await player.wait_for("new_question", EV)
        assert pin["imageUrls"] == {str(bg["id"]): bg["url"]}
        await player.emit("submit_answer", {"question_id": pin["questionId"],
                                            "answer_data": {"x": 1, "y": -2}})
        got = await player.wait_for("answer_received", EV)
        assert got["pointsAwarded"] == 3
        await host.emit("host_advance", {})
        await host.wait_for("question_results", EV)
        await host.emit("host_advance", {})
        over = await host.wait_for("game_over", EV)
        assert over["imageUrls"] == {**want_mc, str(bg["id"]): bg["url"]}
        pover = await player.wait_for("game_over", EV)
        assert pover["imageUrls"] == over["imageUrls"]
    finally:
        await host.disconnect()
        await player.disconnect()
    detail = httpx.get(f"{base}/api/player/past-quizzes/{sid}", headers=H(t8["player"])).json()
    assert detail["image_urls"] == over["imageUrls"]


async def test_host_reconnect_sync_state_has_image_urls(t8):
    base, a_tok = t8["base"], t8["a"]
    p = up(t8)
    gid = new_quiz(t8)
    add_q(t8, gid, mc_img("mc", prompt_image=p["id"]))
    code, sid = start_room(t8, a_tok, gid)
    host = await join(base, a_tok, code, "HOST", "host")
    player = await join(base, t8["player"], code, "PLAYER", "player")
    try:
        await host.emit("host_advance", {})
        await player.wait_for("new_question", EV)
        again = SocketClient(base, a_tok, "host-again")
        await again.connect()
        await again.emit("join_room", {"room_code": code, "role": "HOST"})
        sync = await again.wait_for("sync_state", EV)
        assert sync["currentQuestion"]["imageUrls"] == {str(p["id"]): p["url"]}
        await again.disconnect()
    finally:
        await host.disconnect()
        await player.disconnect()
        end(t8, a_tok, sid)


# ---------------------------------------------------------------------------
# 8. Concurrency (each test accepts exactly the two outcomes of §12.8)
# ---------------------------------------------------------------------------


def _ok_or_fail(*responses: httpx.Response) -> None:
    for r in responses:
        assert r.status_code < 500 and "Busy, try again" not in r.text, (r.status_code, r.text)


async def test_concurrent_update_adding_image_vs_delete(t8):
    """(a) Update adding I vs delete of I."""
    base = t8["base"]
    image = up(t8)
    gid = new_quiz(t8)
    qid = add_q(t8, gid, mc_img("a")).json()["id"]
    body = {"config": {"options": ["A", "B"], "promptImageId": image["id"]}}
    async with httpx.AsyncClient(base_url=base, headers=H(t8["a"]), timeout=10) as client:
        async with GameRowLock(t8["c1"], table="courses") as lock:
            put = asyncio.ensure_future(client.put(f"/api/host/games/{gid}/questions/{qid}", json=body))
            dele = asyncio.ensure_future(client.delete(f"/api/host/images/{image['id']}"))
            await lock.wait_for_waiters(2)
        rp, rd = await asyncio.gather(put, dele)
    _ok_or_fail(rp, rd)
    config = questions(t8, gid)[0]["config"]
    if rp.status_code == 200:
        assert rd.status_code == 409 and "used by" in rd.text
        assert config.get("promptImageId") == image["id"] and image["id"] in listing(t8)
    else:
        # The delete won: a hard delete removes the row, so the update names the field.
        assert rd.status_code == 204 and rp.status_code == 422
        assert rp.json()["detail"].startswith("promptImageId: image")
        assert "promptImageId" not in config and view(t8, image["url"]).status_code == 404


async def test_concurrent_room_creation_vs_replace(t8):
    """(b) Room creation vs replace of I."""
    base = t8["base"]
    image = up(t8)
    gid = new_quiz(t8)
    add_q(t8, gid, mc_img("b", prompt_image=image["id"]))
    async with httpx.AsyncClient(base_url=base, headers=H(t8["a"]), timeout=10) as client:
        async with GameRowLock(t8["c1"], table="courses") as lock:
            room = asyncio.ensure_future(client.post("/api/game/rooms", json={"game_id": gid}))
            rep = asyncio.ensure_future(client.put(
                f"/api/host/images/{image['id']}",
                files={"file": ("b.png", img(color="blue"), "image/png")},
            ))
            await lock.wait_for_waiters(2)
        rr, rp = await asyncio.gather(room, rep)
    _ok_or_fail(rr, rp)
    assert rr.status_code == 201, rr.text
    sid, code = rr.json()["session_id"], rr.json()["room_code"]
    try:
        config = questions(t8, gid)[0]["config"]
        if rp.status_code == 409:
            assert "A room is open for" in rp.text
            assert config["promptImageId"] == image["id"] and image["id"] in listing(t8)
        else:
            assert rp.status_code == 201
            new_id = rp.json()["id"]
            assert config["promptImageId"] == new_id
            assert view(t8, image["url"]).status_code == 404  # pruned
            host = await join(base, t8["a"], code, "HOST", "host")
            try:
                await host.emit("host_advance", {})
                await host.wait_for("new_question", EV)
            finally:
                await host.disconnect()
            snap = sql(f"SELECT JSON_EXTRACT(config, '$.promptImageId') FROM session_questions "
                       f"WHERE session_id = '{sid}'")
            assert snap == str(new_id)
    finally:
        end(t8, t8["a"], sid)


async def test_concurrent_replace_vs_new_question(t8):
    """(c) Replace of I vs adding a new question referencing I to another quiz."""
    base = t8["base"]
    image = up(t8)
    g1 = new_quiz(t8)
    add_q(t8, g1, mc_img("c1", prompt_image=image["id"]))
    g2 = new_quiz(t8)
    async with httpx.AsyncClient(base_url=base, headers=H(t8["a"]), timeout=10) as client:
        async with GameRowLock(t8["c1"], table="courses") as lock:
            rep = asyncio.ensure_future(client.put(
                f"/api/host/images/{image['id']}",
                files={"file": ("c.png", img(color="blue"), "image/png")},
            ))
            add = asyncio.ensure_future(client.post(
                f"/api/host/games/{g2}/questions", json=mc_img("c2", prompt_image=image["id"])
            ))
            await lock.wait_for_waiters(2)
        rp, ra = await asyncio.gather(rep, add)
    _ok_or_fail(rp, ra)
    assert rp.status_code == 201, rp.text
    new_id = rp.json()["id"]
    if ra.status_code == 422:
        # I lost its only reference to the replace, so prune removed the row: "not found".
        # (Were I still kept by history it would read "image is not current".)
        assert ra.json()["detail"].startswith("promptImageId: image"), ra.text
        assert view(t8, image["url"]).status_code == 404
    else:
        assert ra.status_code == 201
        assert questions(t8, g2)[0]["config"]["promptImageId"] == new_id
    for gid in (g1, g2):
        for item in questions(t8, gid):
            assert item["config"].get("promptImageId") != image["id"]


async def test_answer_while_delete_is_refused(t8):
    """(d) A player answers while a delete of the question's image runs (and is refused)."""
    base = t8["base"]
    image = up(t8)
    gid = new_quiz(t8)
    add_q(t8, gid, mc_img("d", prompt_image=image["id"]))
    code, sid = start_room(t8, t8["a"], gid)
    host = await join(base, t8["a"], code, "HOST", "host")
    player = await join(base, t8["player"], code, "PLAYER", "player")
    try:
        await host.emit("host_advance", {})
        nq = await player.wait_for("new_question", EV)
        async with httpx.AsyncClient(base_url=base, headers=H(t8["a"]), timeout=10) as client:
            dele = asyncio.ensure_future(client.delete(f"/api/host/images/{image['id']}"))
            await player.emit("submit_answer", {"question_id": nq["questionId"],
                                                "answer_data": {"selectedIndex": 0}})
            got = await player.wait_for("answer_received", EV)
            rd = await dele
        assert rd.status_code == 409, rd.text
        assert got["pointsAwarded"] == 1
        assert sql(f"SELECT COUNT(*) FROM session_scores WHERE session_id = '{sid}'") == "1"
        assert image["id"] in listing(t8)
    finally:
        await host.disconnect()
        await player.disconnect()
        end(t8, t8["a"], sid)


async def test_two_replaces_of_one_image(t8):
    """(e) Exactly one replace wins; the other gets 409; one new row."""
    base = t8["base"]
    image = up(t8)
    async with httpx.AsyncClient(base_url=base, headers=H(t8["a"]), timeout=10) as client:
        async with GameRowLock(t8["c1"], table="courses") as lock:
            calls = [
                asyncio.ensure_future(client.put(
                    f"/api/host/images/{image['id']}",
                    files={"file": (f"{c}.png", img(color=c), "image/png")},
                ))
                for c in ("blue", "green")
            ]
            await lock.wait_for_waiters(2)
        results = await asyncio.gather(*calls)
    _ok_or_fail(*results)
    assert sorted(r.status_code for r in results) == [201, 409], [r.text for r in results]
    loser = next(r for r in results if r.status_code == 409)
    assert "This image was replaced or deleted; reload" in loser.text
    assert len(listing(t8)) == 1
