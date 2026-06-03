#!/usr/bin/env bash
# Record a live/webinar page with visible Chromium, Xvfb, and PulseAudio monitor audio.
set -euo pipefail

URL=${1:?usage: record_live_page.sh <url> [outdir] [max_seconds]}
OUTDIR=${2:-"$HOME/webinar-recordings/$(date +%Y%m%d-%H%M%S)"}
MAX_SECONDS=${3:-14400}
DISPLAY_ID=${DISPLAY_ID:-:120}
WIDTH=${WIDTH:-1366}
HEIGHT=${HEIGHT:-768}
FPS=${FPS:-12}
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

mkdir -p "$OUTDIR"
cd "$OUTDIR"
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/tmp/hermes-live-capture-runtime}
mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
export DISPLAY="$DISPLAY_ID"
unset PULSE_SERVER

cleanup() {
  set +e
  [[ -n "${BROWSER_PID:-}" ]] && kill "$BROWSER_PID" >/dev/null 2>&1
  [[ -n "${XVFB_PID:-}" ]] && kill "$XVFB_PID" >/dev/null 2>&1
  pkill -f "Xvfb $DISPLAY_ID" >/dev/null 2>&1
}
trap cleanup EXIT

for c in Xvfb pulseaudio pactl parec ffmpeg ffprobe python3; do
  command -v "$c" >/dev/null || { echo "missing_command=$c"; exit 2; }
done

pkill -f "Xvfb $DISPLAY_ID" >/dev/null 2>&1 || true
pulseaudio --kill >/dev/null 2>&1 || true
pkill -u "$USER" pulseaudio >/dev/null 2>&1 || true

Xvfb "$DISPLAY_ID" -screen 0 "${WIDTH}x${HEIGHT}x24" -ac +extension RANDR >"$OUTDIR/xvfb.log" 2>&1 & XVFB_PID=$!
sleep 0.7
pulseaudio --start --exit-idle-time=-1 --log-target="file:$OUTDIR/pulseaudio.log"
sleep 0.7
pactl load-module module-null-sink sink_name=webinar sink_properties=device.description=WebinarSink >"$OUTDIR/pulse-module-id" || true
pactl set-default-sink webinar
pactl info >"$OUTDIR/pactl-info.txt" || true
pactl list short sources >"$OUTDIR/pactl-sources.txt" || true

SAFE_TS=$(date +%Y%m%d-%H%M%S)
AV="$OUTDIR/live-av-$SAFE_TS.mp4"
AUDIO="$OUTDIR/live-audio-$SAFE_TS.wav"
BROWSER_LOG="$OUTDIR/browser-run.log"
FFMPEG_LOG="$OUTDIR/ffmpeg-record.log"

{
  echo "start=$(date -Is)"
  echo "url=$URL"
  echo "outdir=$OUTDIR"
  echo "av=$AV"
  echo "audio=$AUDIO"
  echo "max_seconds=$MAX_SECONDS"
  echo "display=$DISPLAY_ID"
} | tee "$OUTDIR/run-state.txt"

python3 "$SCRIPT_DIR/browser_live_keepalive.py" "$URL" "$OUTDIR" "$MAX_SECONDS" >"$BROWSER_LOG" 2>&1 & BROWSER_PID=$!
sleep 1

set +o pipefail
parec -d webinar.monitor --format=s16le --rate=44100 --channels=2 | \
  ffmpeg -y -hide_banner -loglevel info \
    -f x11grab -video_size "${WIDTH}x${HEIGHT}" -framerate "$FPS" -i "$DISPLAY_ID" \
    -f s16le -ar 44100 -ac 2 -i pipe:0 \
    -t "$MAX_SECONDS" \
    -c:v libx264 -preset veryfast -crf 30 -pix_fmt yuv420p \
    -c:a aac -b:a 128k "$AV" >"$FFMPEG_LOG" 2>&1
REC_RC=$?
set -o pipefail

{
  echo "record_rc=$REC_RC"
  echo "ended=$(date -Is)"
} >> "$OUTDIR/run-state.txt"

ffprobe -hide_banner "$AV" >"$OUTDIR/ffprobe-av.txt" 2>&1 || true
ffmpeg -y -hide_banner -loglevel error -i "$AV" -vn -ac 1 -ar 16000 "$AUDIO" || true
ffprobe -hide_banner "$AUDIO" >"$OUTDIR/ffprobe-audio.txt" 2>&1 || true

python3 - <<PY | tee "$OUTDIR/audio-rms.txt" || true
import wave, audioop, pathlib
p=pathlib.Path('$AUDIO')
if not p.exists():
    print('audio_missing')
else:
    w=wave.open(str(p),'rb'); data=w.readframes(w.getnframes())
    print('audio_wav='+str(p))
    print('frames='+str(w.getnframes()))
    print('rate='+str(w.getframerate()))
    print('rms='+str(audioop.rms(data,w.getsampwidth()) if data else 0))
PY

echo "AV=$AV"
echo "AUDIO=$AUDIO"
echo "OUTDIR=$OUTDIR"
echo "RMS=$(tail -1 "$OUTDIR/audio-rms.txt" 2>/dev/null || true)"
