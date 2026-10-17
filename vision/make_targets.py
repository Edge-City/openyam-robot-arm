"""Generate printable A4 targets at 300 dpi into vision/targets/.

  charuco_board_A4.pdf/.png   ChArUco calibration board (A4 landscape)
  aruco_tags_A4.pdf           ArUco tags, page 1 at 40 mm, page 2 at 25 mm
  aruco_tags_40mm.png, aruco_tags_25mm.png
  charuco_board.json, tags.json   exact printed sizes (read by the other scripts)

Every page carries a 50 mm check ruler. Print at 100% / "actual size".

  .venv/bin/python vision/make_targets.py
"""
from __future__ import annotations

import json

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from targets_spec import (BOARD_DICT, BOARD_MARKER_MM, BOARD_SQUARE_MM, BOARD_SQUARES, DPI, PX_PER_MM,
                          TAG_DICT, TAG_IDS, TAG_SIZES_MM, TARGETS_DIR, aruco_dict, make_board, role)

A4_MM = (210.0, 297.0)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def px(mm: float) -> int:
    return int(round(mm * PX_PER_MM))


def font(pt: float, bold=False):
    size = int(round(pt / 72 * DPI))
    try:
        return ImageFont.truetype(FONT_BOLD if bold else FONT, size)
    except OSError:
        return ImageFont.load_default(size)


def page(landscape=False) -> Image.Image:
    w, h = A4_MM[::-1] if landscape else A4_MM
    return Image.new("L", (px(w), px(h)), 255)


def ruler(d: ImageDraw.ImageDraw, x_mm: float, y_mm: float, length_mm: int = 50):
    """Horizontal ruler; the baseline is at y_mm, ticks hang upward."""
    x0, y0 = px(x_mm), px(y_mm)
    d.line([(x0, y0), (px(x_mm + length_mm), y0)], fill=0, width=3)
    f = font(6)
    for i in range(length_mm + 1):
        x = px(x_mm + i)
        tick = 5 if i % 10 == 0 else 3.5 if i % 5 == 0 else 2
        d.line([(x, y0), (x, y0 - px(tick))], fill=0, width=3 if i % 10 == 0 else 2)
        if i % 10 == 0:
            d.text((x, y0 - px(tick) - 4), str(i), font=f, fill=0, anchor="mb")
    d.text((x0, y0 + px(1.5)), f"{length_mm} mm check: must measure exactly {length_mm}.0 mm "
           "(otherwise rescale, see README)", font=font(6.5), fill=0, anchor="la")


def dashed_rect(d: ImageDraw.ImageDraw, box, dash=px(2), fill=170):
    x0, y0, x1, y1 = box
    for x in range(x0, x1, 2 * dash):
        d.line([(x, y0), (min(x + dash, x1), y0)], fill=fill, width=2)
        d.line([(x, y1), (min(x + dash, x1), y1)], fill=fill, width=2)
    for y in range(y0, y1, 2 * dash):
        d.line([(x0, y), (x0, min(y + dash, y1))], fill=fill, width=2)
        d.line([(x1, y), (x1, min(y + dash, y1))], fill=fill, width=2)


def make_board_page():
    cols, rows = BOARD_SQUARES
    sq_px = px(BOARD_SQUARE_MM)
    square_mm = sq_px / PX_PER_MM          # exact printed size of one square
    marker_mm = square_mm * BOARD_MARKER_MM / BOARD_SQUARE_MM
    board = make_board(square_mm, marker_mm)
    art = board.generateImage((cols * sq_px, rows * sq_px), marginSize=0, borderBits=1)

    im = page(landscape=True)
    W, H = im.size
    bx, by = (W - art.shape[1]) // 2, px(24)
    im.paste(Image.fromarray(art), (bx, by))
    d = ImageDraw.Draw(im)
    d.text((px(15), px(8)), "OpenYAM ChArUco calibration board", font=font(14, True), fill=0)
    d.text((px(15), px(15)),
           f"{BOARD_DICT}, {cols} x {rows} squares, square {square_mm:.2f} mm, marker {marker_mm:.2f} mm "
           f"(as printed at 100%, {DPI} dpi). Board {cols * square_mm:.1f} x {rows * square_mm:.1f} mm.",
           font=font(8), fill=0)
    yb = (by + art.shape[0]) / PX_PER_MM
    ruler(d, 15, yb + 13)
    notes = ["Print at 100% / Actual size (no 'fit to page'). Glue flat to stiff cardboard or foam board.",
             "Measure a square with calipers: if it is not the value above, pass --square-mm <measured> to",
             "calibrate_intrinsics.py. Matte paper avoids glare. Do not bend; keep the white border."]
    for i, t in enumerate(notes):
        d.text((px(130), px(yb + 6 + 5 * i)), t, font=font(7), fill=0)
    info = {"dict": BOARD_DICT, "squares": [cols, rows], "square_mm": round(square_mm, 4),
            "marker_mm": round(marker_mm, 4), "dpi": DPI, "page": "A4 landscape",
            "origin_px": [bx, by], "square_px": sq_px}
    return im, info


def make_tag_page(size_mm: float, cell_mm, cols: int):
    tag_px = px(size_mm)
    exact = tag_px / PX_PER_MM
    im = page()
    d = ImageDraw.Draw(im)
    d.text((px(15), px(8)), f"OpenYAM ArUco tags, {exact:.2f} mm", font=font(14, True), fill=0)
    d.text((px(15), px(15)), f"{TAG_DICT}. Tag size = outer edge of the black square = {exact:.2f} mm at 100%.",
           font=font(8), fill=0)
    d.text((px(15), px(19.5)), "Cut on the dashed lines and keep the white margin. Use ONE copy of each ID per scene.",
           font=font(8), fill=0)
    ruler(d, 15, 284)
    gx = (A4_MM[0] - cols * cell_mm[0]) / 2
    gy = 30.0
    centers = {}
    for k, tid in enumerate(TAG_IDS):
        r, c = divmod(k, cols)
        x0, y0 = gx + c * cell_mm[0], gy + r * cell_mm[1]
        dashed_rect(d, (px(x0), px(y0), px(x0 + cell_mm[0]), px(y0 + cell_mm[1])))
        tx = px(x0 + cell_mm[0] / 2) - tag_px // 2
        ty = px(y0 + (cell_mm[0] - size_mm) / 2 * 0.8)
        m = cv2.aruco.generateImageMarker(aruco_dict(TAG_DICT), tid, tag_px, borderBits=1)
        im.paste(Image.fromarray(m), (tx, ty))
        centers[tid] = [(tx + tag_px / 2) / PX_PER_MM, (ty + tag_px / 2) / PX_PER_MM]
        ly = ty + tag_px + px(2)
        d.text((px(x0 + cell_mm[0] / 2), ly), f"ID {tid}  ({exact:.0f} mm)", font=font(9 if size_mm > 30 else 7, True),
               fill=0, anchor="ma")
        d.text((px(x0 + cell_mm[0] / 2), ly + px(4.5 if size_mm > 30 else 3.5)), role(tid),
               font=font(7 if size_mm > 30 else 6), fill=0, anchor="ma")
    return im, {"size_mm": round(exact, 4), "px": tag_px, "centers_mm": centers}


def main():
    TARGETS_DIR.mkdir(exist_ok=True)
    board_im, board_info = make_board_page()
    board_im.save(TARGETS_DIR / "charuco_board_A4.png", dpi=(DPI, DPI))
    board_im.save(TARGETS_DIR / "charuco_board_A4.pdf", resolution=float(DPI))
    (TARGETS_DIR / "charuco_board.json").write_text(json.dumps(board_info, indent=2) + "\n")

    p40, i40 = make_tag_page(TAG_SIZES_MM[0], (60.0, 60.0), 3)
    p25, i25 = make_tag_page(TAG_SIZES_MM[1], (45.0, 42.0), 4)
    p40.save(TARGETS_DIR / "aruco_tags_40mm.png", dpi=(DPI, DPI))
    p25.save(TARGETS_DIR / "aruco_tags_25mm.png", dpi=(DPI, DPI))
    p40.save(TARGETS_DIR / "aruco_tags_A4.pdf", resolution=float(DPI), save_all=True, append_images=[p25])
    tags = {"dict": TAG_DICT, "dpi": DPI, "roles": {str(i): role(i) for i in TAG_IDS},
            "pages": {"40mm": i40, "25mm": i25}}
    (TARGETS_DIR / "tags.json").write_text(json.dumps(tags, indent=2) + "\n")

    print(f"board: square {board_info['square_mm']} mm, marker {board_info['marker_mm']} mm")
    print(f"tags: {i40['size_mm']} mm and {i25['size_mm']} mm")
    for f in sorted(TARGETS_DIR.iterdir()):
        print(" ", f.relative_to(TARGETS_DIR.parent.parent), f"{f.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
