"""Render the printable robot-table sign (A4, 300 dpi) with a QR code to this repo.

    uv run --with "qrcode[pil]" sign/make_sign.py [url]

Writes sign/robot-table-sign.pdf, sign/robot-table-sign.png and sign/qr.png.
"""
import sys
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

URL = sys.argv[1] if len(sys.argv) > 1 else "https://github.com/Edge-City/openyam-robot-arm"
OUT = Path(__file__).resolve().parent
DPI = 300
W, H = 2480, 3508  # A4 portrait at 300 dpi
MARGIN = 200


def font(size, bold=False):
    candidates = [
        ("/System/Library/Fonts/HelveticaNeue.ttc", 1 if bold else 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ]
    for path, index in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size, index=index)
    return ImageFont.load_default(size)


def centered(draw, y, text, f, fill):
    width = draw.textlength(text, font=f)
    draw.text(((W - width) / 2, y), text, font=f, fill=fill)
    return y + f.size * 1.25


qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
qr.add_data(URL)
qr.make(fit=True)
code = qr.make_image(fill_color="black", back_color="white").convert("RGB")
code.save(OUT / "qr.png")

sign = Image.new("RGB", (W, H), "white")
draw = ImageDraw.Draw(sign)
ink, muted = (17, 17, 17), (90, 90, 90)

y = MARGIN
y = centered(draw, y, "Drive a robot arm", font(190, bold=True), ink)
y = centered(draw, y + 10, "Anvil OpenYAM  ·  DimOS  ·  Edge City", font(80), muted)

size = 1500
qr_big = code.resize((size, size), Image.NEAREST)
sign.paste(qr_big, ((W - size) // 2, int(y + 80)))
y += 80 + size + 40
y = centered(draw, y, "Scan for the setup guide", font(96, bold=True), ink)
y = centered(draw, y, URL.removeprefix("https://"), font(64), muted)

steps = [
    "1  Install DimOS on your laptop (no GPU needed)",
    "2  Run the virtual arm with --simulation",
    "3  Drive it with W A S D Q E  ·  [ ] for the gripper",
]
y += 90
for step in steps:
    draw.text((MARGIN + 120, y), step, font=font(72), fill=ink)
    y += 120

y += 50
y = centered(draw, y, "Real arm: ask at the table first. Keep the e-stop in reach.", font(62, bold=True), (170, 30, 30))

sign.save(OUT / "robot-table-sign.png", dpi=(DPI, DPI))
sign.save(OUT / "robot-table-sign.pdf", resolution=DPI)
print(f"QR -> {URL}\nWrote {OUT / 'robot-table-sign.pdf'}")
