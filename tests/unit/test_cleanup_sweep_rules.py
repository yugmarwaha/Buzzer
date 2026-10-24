"""
Unit tests for the name rules of `backend/scripts/cleanup_test_quiz.py --sweep`.

The sweep deletes data for real at the start and end of every integration run, so these
tests pin down that it only matches what the suite creates and never real courses or users.

Run with:
    PYTHONPATH=backend python -m pytest tests/unit/test_cleanup_sweep_rules.py -q
"""
from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace

import pytest

# Load by path: the repo root also has a `scripts/` package.
_PATH = pathlib.Path(__file__).parents[2] / "backend" / "scripts" / "cleanup_test_quiz.py"
_spec = importlib.util.spec_from_file_location("cleanup_test_quiz", _PATH)
cleanup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cleanup)


def course(name: str, semester: str = "Test"):
    return SimpleNamespace(name=name, semester=semester)


def user(role="USER", username=None, netid=None, email=None):
    return SimpleNamespace(role=role, username=username, netid=netid, email=email)


@pytest.mark.parametrize(
    "name",
    ["Test Course 0a1b2c3d", "Real-looking course 8ae725"],
)
def test_sweeps_suite_courses(name):
    assert cleanup.is_sweepable_course(course(name))


@pytest.mark.parametrize(
    "name, semester",
    [
        ("Demo Course", "Demo"),
        ("Test Course 0a1b2c3d", "Fall 2026"),  # right name, real semester
        ("Real-looking course 8ae725", "F26"),
        ("Test Course 0A1B2C3D", "Test"),  # upper-case hex is not the fixture's naming
        ("Test Course 0a1b2c3d extra", "Test"),
        ("My Test Course 0a1b2c3d", "Test"),
        ("Computer Networks", "Test"),
    ],
)
def test_keeps_other_courses(name, semester):
    assert not cleanup.is_sweepable_course(course(name, semester))


def test_quiz_cleanup_still_refuses_guard_course():
    # --game-id must keep refusing the "Real-looking" course; only --sweep removes it.
    assert cleanup.is_test_course_name("Test Course 0a1b2c3d")
    assert not cleanup.is_test_course_name("Real-looking course 8ae725")


@pytest.mark.parametrize(
    "u",
    [
        user(username="t4_host_a"),
        user(username="t4_player2"),
        user(netid="t4m1a2b3c"),
        user(netid="t4d0f9e8d"),
        user(role="GUEST", email="testplayer0.ab12cd@example.com"),
        user(role="GUEST", email="testplayer12.ab12cd@example.com"),
    ],
)
def test_sweeps_suite_users(u):
    assert cleanup.is_test_user(u)


@pytest.mark.parametrize(
    "u",
    [
        user(role="ADMIN", username="t4_host_a"),  # never an admin
        user(role="ADMIN", username="admin23"),
        user(username="ymarwaha", netid="ymarwaha"),
        user(netid="t4"),
        user(netid="t4m1a2b3c4"),  # too long
        user(role="GUEST", email="student@example.com"),
        user(role="USER", email="testplayer0.ab12cd@example.com"),  # only guests by email
        user(role="GUEST", email="testplayer0.ab12cd@wisc.edu"),
        user(role="GUEST", email=None),
    ],
)
def test_keeps_other_users(u):
    assert not cleanup.is_test_user(u)
