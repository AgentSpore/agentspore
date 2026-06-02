#!/usr/bin/env bash
# 8 slides × 3.7s + xfades 0.5s = 26.1s
set -e
cd "$(dirname "$0")"

SLIDES=(renders/slide-0{1,2,3,4,5,6,7,8}.png)
MUSIC="assets/bg-music.mp3"
OUT="renders/carousel.mp4"

# Each slide 3.7s on screen, fade 0.5s between → total = 8*3.7 - 7*0.5 = 26.1s
# offsets: 3.2, 6.4, 9.6, 12.8, 16.0, 19.2, 22.4
FILTER="
[0:v][1:v]xfade=transition=fade:duration=0.5:offset=3.2[v01];
[v01][2:v]xfade=transition=fade:duration=0.5:offset=6.4[v02];
[v02][3:v]xfade=transition=fade:duration=0.5:offset=9.6[v03];
[v03][4:v]xfade=transition=fade:duration=0.5:offset=12.8[v04];
[v04][5:v]xfade=transition=fade:duration=0.5:offset=16.0[v05];
[v05][6:v]xfade=transition=fade:duration=0.5:offset=19.2[v06];
[v06][7:v]xfade=transition=fade:duration=0.5:offset=22.4[vraw];
[vraw]fade=t=in:st=0:d=0.6,fade=t=out:st=25.5:d=0.6[vout];
[8:a]afade=t=in:st=0:d=1.0,afade=t=out:st=25:d=1.0[aout]
"

ffmpeg -y \
  -loop 1 -t 3.7 -i "${SLIDES[0]}" \
  -loop 1 -t 3.7 -i "${SLIDES[1]}" \
  -loop 1 -t 3.7 -i "${SLIDES[2]}" \
  -loop 1 -t 3.7 -i "${SLIDES[3]}" \
  -loop 1 -t 3.7 -i "${SLIDES[4]}" \
  -loop 1 -t 3.7 -i "${SLIDES[5]}" \
  -loop 1 -t 3.7 -i "${SLIDES[6]}" \
  -loop 1 -t 3.7 -i "${SLIDES[7]}" \
  -i "$MUSIC" \
  -filter_complex "$FILTER" \
  -map "[vout]" -map "[aout]" \
  -c:v libx264 -pix_fmt yuv420p -r 30 -preset medium -crf 20 \
  -c:a aac -b:a 192k \
  -t 26.1 \
  "$OUT"

ls -lh "$OUT"
