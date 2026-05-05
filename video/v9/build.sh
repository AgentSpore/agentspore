#!/bin/bash
# Build per-scene clips and final video.
set -euo pipefail

ROOT="/Users/exzent/projects/startups/projects/Platform/agentsspore/video/v9"
SCENES="$ROOT/scenes"
VO="$ROOT/vo"
CLIPS="$ROOT/clips"
FINAL="/Users/exzent/Desktop/FINAL_v9.mp4"
mkdir -p "$CLIPS"

# scene_num : pad_seconds (extra time after VO)
PADS=(0 1.2 1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.5)

# Build each scene clip with subtle zoom-in (ken burns) + VO
for i in $(seq 1 10); do
  ii=$(printf "%02d" $i)
  png="$SCENES/s$ii.png"
  vo="$VO/vo_$ii.mp3"
  out="$CLIPS/clip_$ii.mp4"

  vo_dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$vo")
  pad=${PADS[$i]}
  total=$(python3 -c "print(round($vo_dur + $pad, 3))")
  frames=$(python3 -c "print(int(round($total * 30)))")

  echo "scene $ii vo=$vo_dur total=$total frames=$frames"

  # zoompan: very gentle zoom 1.0->1.06 over the clip; slight pan
  ffmpeg -y -loglevel error \
    -loop 1 -framerate 30 -t "$total" -i "$png" \
    -i "$vo" \
    -filter_complex "
      [0:v]scale=2304:1296,
      zoompan=z='min(zoom+0.0006,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps=30,
      format=yuv420p[v]
    " \
    -map "[v]" -map 1:a \
    -c:v libx264 -preset medium -crf 19 -profile:v high -pix_fmt yuv420p \
    -c:a aac -b:a 192k -ar 48000 -ac 2 \
    -t "$total" \
    "$out"
done

# Concat with crossfade transitions (xfade)
# Build xfade chain
INPUTS=""
for i in $(seq 1 10); do
  ii=$(printf "%02d" $i)
  INPUTS="$INPUTS -i $CLIPS/clip_$ii.mp4"
done

# Compute cumulative offsets for xfade (each fade 0.4s)
FADE_DUR=0.4
python3 - <<'PY' > "$ROOT/.xfade_filter.txt"
import subprocess, json, os
clips_dir = "/Users/exzent/projects/startups/projects/Platform/agentsspore/video/v9/clips"
durs = []
for i in range(1, 11):
    p = f"{clips_dir}/clip_{i:02d}.mp4"
    out = subprocess.check_output([
        "ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0", p
    ]).decode().strip()
    durs.append(float(out))

fade = 0.4
v_chain = []
a_chain = []
prev_v = "[0:v]"
prev_a = "[0:a]"
offset = durs[0] - fade
for i in range(1, 10):
    vo_label = f"[v{i}]"
    ao_label = f"[a{i}]"
    v_chain.append(f"{prev_v}[{i}:v]xfade=transition=fade:duration={fade}:offset={offset:.3f}{vo_label}")
    a_chain.append(f"{prev_a}[{i}:a]acrossfade=d={fade}{ao_label}")
    prev_v = vo_label
    prev_a = ao_label
    offset += durs[i] - fade

filt = ";".join(v_chain + a_chain)
print(filt)
print(prev_v, prev_a, sep=":", file=open("/Users/exzent/projects/startups/projects/Platform/agentsspore/video/v9/.xfade_maps.txt","w"))
PY

FILTER=$(cat "$ROOT/.xfade_filter.txt")
MAPS=$(cat "$ROOT/.xfade_maps.txt")
VMAP=${MAPS%:*}
AMAP=${MAPS#*:}

echo "final maps v=$VMAP a=$AMAP"

ffmpeg -y -loglevel error \
  $INPUTS \
  -filter_complex "$FILTER" \
  -map "$VMAP" -map "$AMAP" \
  -c:v libx264 -preset slow -crf 19 -profile:v high -pix_fmt yuv420p \
  -c:a aac -b:a 192k -ar 48000 -ac 2 \
  "$FINAL"

echo "DONE: $FINAL"
ffprobe -v error -show_entries format=duration -of csv=p=0 "$FINAL"
