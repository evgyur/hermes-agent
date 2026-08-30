#!/usr/bin/env python3
"""Provider-neutral Zoom/Google Meet state and mic control over local Chrome CDP."""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from typing import Any

import websocket


def normalize_meet_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def matches_tab(provider: str, key: str, url: str) -> bool:
    if provider == "zoom":
        digits = re.sub(r"\D", "", key)
        return bool(digits and f"/wc/{digits}/" in url)
    if provider == "google":
        wanted = normalize_meet_key(key)
        match = re.search(r"meet\.google\.com/([a-z0-9-]+)", url.lower())
        return bool(match and normalize_meet_key(match.group(1)) == wanted)
    return False


def walk(frame: dict[str, Any]):
    yield frame
    for child in frame.get("childFrames", []):
        yield from walk(child)


ZOOM_STATE = r"""(()=>{
  const buttons=[...document.querySelectorAll('button')];
  const body=document.body.innerText||'';
  const labels=buttons.map(b=>b.getAttribute('aria-label')||'');
  const participantsAria=labels.find(x=>/participants list pane/i.test(x))||'';
  const countMatch=participantsAria.match(/([0-9]+)[ ]+partic/i);
  return {
    provider:'zoom', inMeeting:labels.some(x=>x.toLowerCase()==='leave'),
    mic:labels.find(x=>/mute my microphone|unmute my microphone/i.test(x))||'',
    recording:body.toLowerCase().includes('recording') || labels.some(x=>/pause recording|stop recording/i.test(x)),
    participantCount:countMatch?Number(countMatch[1]):0,
    hasChip:body.includes('Евгений "Chip"'), hasSigurd:body.includes('Сигурд AI')
  };
})()"""

MEET_STATE = r"""(()=>{
  const buttons=[...document.querySelectorAll('button')];
  const body=document.body.innerText||'';
  const labels=buttons.map(b=>b.getAttribute('aria-label')||'');
  const countLabel=labels.find(x=>/(show everyone|participants|people).*[0-9]|[0-9].*(participants|people)/i.test(x))||'';
  const countMatch=countLabel.match(/([0-9]+)/);
  return {
    provider:'google', inMeeting:labels.some(x=>/^leave call$/i.test(x)),
    mic:labels.find(x=>/turn (on|off) microphone/i.test(x))||'',
    recording:/recording/i.test(body) || labels.some(x=>/stop recording/i.test(x)),
    participantCount:countMatch?Number(countMatch[1]):0,
    hasChip:/Евгений\s*("Chip")?|Evgeny\s*("Chip")?/i.test(body),
    hasSigurd:/Сигурд AI|Sigurd AI/i.test(body)
  };
})()"""


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print("usage: meeting_state.py {zoom|google} {status|mute|unmute|ensure-recording} <meeting-key>", file=sys.stderr)
        return 2
    provider, action, key = argv[1], argv[2], argv[3]
    if provider not in {"zoom", "google"} or action not in {"status", "mute", "unmute", "ensure-recording"}:
        return 2
    tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:18800/json", timeout=5).read())
    target = next((tab for tab in tabs if tab.get("type") == "page" and matches_tab(provider, key, tab.get("url", ""))), None)
    if not target:
        raise SystemExit("MEETING_TAB_NOT_FOUND")
    ws = websocket.create_connection(target["webSocketDebuggerUrl"], suppress_origin=True, timeout=10)
    seq = 0

    def call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        nonlocal seq
        seq += 1
        ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
        while True:
            result = json.loads(ws.recv())
            if result.get("id") == seq:
                return result

    def evaluate(expression: str, context_id: int):
        result = call("Runtime.evaluate", {"expression": expression, "contextId": context_id, "returnByValue": True})
        return result.get("result", {}).get("result", {}).get("value")

    tree = call("Page.getFrameTree")["result"]["frameTree"]
    if provider == "zoom":
        frame = next((item for item in walk(tree) if matches_tab(provider, key, item.get("frame", {}).get("url", "")) and item.get("frame", {}).get("parentId")), None)
        if not frame:
            raise SystemExit("MEETING_FRAME_NOT_FOUND")
    else:
        frame = tree
    world = call("Page.createIsolatedWorld", {"frameId": frame["frame"]["id"], "worldName": "sigurd-gpt-voice"})
    context_id = world["result"]["executionContextId"]
    state_expr = ZOOM_STATE if provider == "zoom" else MEET_STATE

    if action in {"mute", "unmute"}:
        if provider == "zoom":
            wanted = "unmute my microphone" if action == "unmute" else "mute my microphone"
            selector = f"(()=>{{const wanted={json.dumps(wanted)};const b=[...document.querySelectorAll('button')].find(x=>(x.getAttribute('aria-label')||'').toLowerCase()===wanted);if(b){{b.click();return true}}return false}})()"
        else:
            wanted = "turn on microphone" if action == "unmute" else "turn off microphone"
            selector = f"(()=>{{const wanted={json.dumps(wanted)};const b=[...document.querySelectorAll('button')].find(x=>(x.getAttribute('aria-label')||'').toLowerCase().startsWith(wanted));if(b){{b.click();return true}}return false}})()"
        evaluate(selector, context_id)
        time.sleep(0.5)
    elif action == "ensure-recording":
        initial = evaluate(state_expr, context_id) or {}
        if not initial.get("recording") and provider == "zoom":
            evaluate("""(()=>{const b=[...document.querySelectorAll('button')].find(x=>((x.getAttribute('aria-label')||'')+' '+(x.innerText||'')).trim().toLowerCase()==='record record');if(b){b.click();return true}return false})()""", context_id)
            time.sleep(1.0)
            evaluate("""(()=>{const b=[...document.querySelectorAll('button')].find(x=>/record to the cloud|record on this computer|start recording/i.test((x.getAttribute('aria-label')||'')+' '+(x.innerText||'')));if(b){b.click();return true}return false})()""", context_id)
            time.sleep(1.0)
        elif not initial.get("recording") and provider == "google":
            opened = evaluate("""(()=>{const b=[...document.querySelectorAll('button')].find(x=>/more options/i.test(x.getAttribute('aria-label')||''));if(b){b.click();return true}return false})()""", context_id)
            if opened:
                time.sleep(0.5)
                evaluate("""(()=>{const nodes=[...document.querySelectorAll('[role=menuitem],button')];const b=nodes.find(x=>/^record meeting$/i.test((x.innerText||'').trim()));if(b){b.click();return true}return false})()""", context_id)
                time.sleep(0.8)
                evaluate("""(()=>{const nodes=[...document.querySelectorAll('button')];const b=nodes.find(x=>/^(start|start recording)$/i.test((x.innerText||'').trim()));if(b){b.click();return true}return false})()""", context_id)
                time.sleep(1.0)

    state = evaluate(state_expr, context_id)
    print(json.dumps(state, ensure_ascii=False))
    ws.close()
    if not state or not state.get("inMeeting"):
        return 3
    if action == "unmute":
        expected = "mute my microphone" if provider == "zoom" else "turn off microphone"
        if expected not in state.get("mic", "").lower(): return 4
    if action == "mute":
        expected = "unmute my microphone" if provider == "zoom" else "turn on microphone"
        if expected not in state.get("mic", "").lower(): return 5
    if action == "ensure-recording" and not state.get("recording"):
        return 6
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
