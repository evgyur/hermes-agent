#!/usr/bin/env bash
set -euo pipefail
export PULSE_SERVER="${PULSE_SERVER:-unix:/run/user/$(id -u)/pulse/native}"
has_sink() { pactl list short sinks | cut -f2 | grep -Fxq "$1"; }
has_source() { pactl list short sources | cut -f2 | grep -Fxq "$1"; }
has_sink meet_output || pactl load-module module-null-sink sink_name=meet_output sink_properties=device.description=Meet_Output >/dev/null
has_sink agent_mic || pactl load-module module-null-sink sink_name=agent_mic sink_properties=device.description=Agent_Mic >/dev/null
has_source agent_mic_source || pactl load-module module-remap-source master=agent_mic.monitor source_name=agent_mic_source source_properties=device.description=Agent_Mic_Source >/dev/null
pactl set-sink-volume agent_mic 100%
pactl set-source-volume agent_mic_source 100%
pactl set-sink-mute agent_mic 0
pactl set-source-mute agent_mic_source 0
