"""Shared definitions of the printed targets (ChArUco board and ArUco tags).

Change sizes here, then re-run make_targets.py and re-print. The scripts read
the measured sizes from the command line when the print scale is off.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2

HERE = Path(__file__).resolve().parent
TARGETS_DIR = HERE / "targets"
CALIB_DIR = HERE / "calib"

DPI = 300
PX_PER_MM = DPI / 25.4

# ChArUco calibration board (A4 landscape)
BOARD_DICT = "DICT_5X5_100"
BOARD_SQUARES = (7, 5)          # (columns, rows)
BOARD_SQUARE_MM = 30.0
BOARD_MARKER_MM = 22.0

# Individual tags
TAG_DICT = "DICT_4X4_50"
TAG_SIZES_MM = (40.0, 25.0)
TAG_IDS = range(10)
TAG_ROLES = {0: "table / world origin", 1: "gripper"}
TAG_ROLES.update({i: "object" for i in range(2, 10)})


def role(tag_id: int) -> str:
    return TAG_ROLES.get(int(tag_id), "unassigned")


def aruco_dict(name: str):
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))


def make_board(square_mm: float = BOARD_SQUARE_MM, marker_mm: float = BOARD_MARKER_MM,
               squares=BOARD_SQUARES, dict_name: str = BOARD_DICT):
    """CharucoBoard in metres."""
    return cv2.aruco.CharucoBoard(tuple(squares), square_mm / 1000.0, marker_mm / 1000.0, aruco_dict(dict_name))


def load_board(path: Path | str | None = None, square_mm: float | None = None, marker_mm: float | None = None):
    """Board from targets/charuco_board.json (written by make_targets.py), with
    optional measured overrides. Returns (board, info dict)."""
    info = {"dict": BOARD_DICT, "squares": list(BOARD_SQUARES),
            "square_mm": BOARD_SQUARE_MM, "marker_mm": BOARD_MARKER_MM}
    p = Path(path) if path else TARGETS_DIR / "charuco_board.json"
    if p.exists():
        info.update(json.loads(p.read_text()))
    if square_mm:
        # Scale the marker with the square unless both were measured.
        if not marker_mm:
            info["marker_mm"] = info["marker_mm"] * square_mm / info["square_mm"]
        info["square_mm"] = square_mm
    if marker_mm:
        info["marker_mm"] = marker_mm
    return make_board(info["square_mm"], info["marker_mm"], info["squares"], info["dict"]), info
