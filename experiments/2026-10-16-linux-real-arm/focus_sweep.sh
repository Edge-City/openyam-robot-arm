#!/usr/bin/env bash
# Sweep manual focus on the USB CAMERA 4K and tile the frames into one contact sheet.
#   ./focus_sweep.sh [/dev/video0] [out.jpg]
# Leaves autofocus back on afterwards.
set -euo pipefail
DEV=${1:-/dev/video0}; OUT=${2:-media/focus-sweep.jpg}
TMP=$(mktemp -d); trap 'rm -rf "$TMP"; v4l2-ctl -d "$DEV" -c focus_automatic_continuous=1' EXIT
v4l2-ctl -d "$DEV" -c focus_automatic_continuous=0
i=0
for f in 0 100 200 300 450 600 800 1023; do
  v4l2-ctl -d "$DEV" -c focus_absolute=$f
  ffmpeg -hide_banner -loglevel error -f v4l2 -input_format mjpeg -video_size 1280x720 -i "$DEV" \
    -vf "select=gte(n\,30),scale=480:270,drawtext=text='focus $f':x=10:y=10:fontsize=28:fontcolor=yellow:box=1:boxcolor=black" \
    -frames:v 1 -update 1 -y "$TMP/$i.jpg"
  i=$((i+1))
done
ffmpeg -hide_banner -loglevel error -i "$TMP/%d.jpg" -vf tile=4x2 -frames:v 1 -update 1 -y "$OUT"
echo "$OUT"
