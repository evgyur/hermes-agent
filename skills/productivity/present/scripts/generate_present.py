#!/usr/bin/env python3
import argparse
import html
import os
import re
import subprocess
from datetime import datetime

from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
POSTCRAFT_DESLOP = os.path.join(ROOT, "vendor", "postcraft", "scripts", "deslop_text.py")
TEMPLATES = {
    "general": os.path.join(ROOT, "templates", "present-default.html"),
    "report": os.path.join(ROOT, "templates", "present-report.html"),
    "offer": os.path.join(ROOT, "templates", "present-offer.html"),
    "h20": os.path.join(ROOT, "templates", "present-h20.html"),
    "slides": os.path.join(ROOT, "templates", "present-slides.html"),
}

REPORT_HINTS = ["отч", "аналит", "исслед", "kpi", "таблиц", "report"]
OFFER_HINTS = ["оффер", "переговор", "proposal", "коммерческ", "deal", "offer"]

LINK_RE = re.compile(r"(https?://[^\s]+|@[A-Za-z0-9_]{4,})")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
SEP_RE = re.compile(r"^[\-_*]{3,}$")
ORDERED_RE = re.compile(r"^\d+[\.)]\s+")
UNORDERED_RE = re.compile(r"^[-*+]\s+")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def rich_plain(text: str) -> str:
    text = text or ""
    out = []
    last = 0
    for m in LINK_RE.finditer(text):
        start, end = m.span()
        token = m.group(0)
        out.append(html.escape(text[last:start]))

        trail = ""
        if token.startswith("http"):
            while token and token[-1] in ".,);:!?]":
                trail = token[-1] + trail
                token = token[:-1]

        if token.startswith("@"):
            href = f"https://t.me/{token[1:]}"
            label = html.escape(token)
        else:
            href = html.escape(token, quote=True)
            label = html.escape(token)

        out.append(f"<a class='rich-link' href=\"{href}\" target=\"_blank\" rel=\"noopener noreferrer\">{label}</a>")
        if trail:
            out.append(html.escape(trail))
        last = end

    out.append(html.escape(text[last:]))
    return "".join(out)


def rich(text: str) -> str:
    s = (text or "").strip()
    s = MD_LINK_RE.sub(lambda m: f"{m.group(1).strip()} ({m.group(2).strip()})", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"__([^_]+)__", r"\1", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", s)
    return rich_plain(s)


def built_in_cleanup(text: str) -> str:
    """Small public-safe fallback when the optional postcraft helper is absent.

    The full local skill may vendor a richer editorial cleanup script. The
    bundled public skill must remain self-contained, so keep this fallback
    conservative: normalize whitespace and remove a few common AI-style lead-ins
    without rewriting the user's content.
    """
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+$", "", s, flags=re.MULTILINE)
    s = re.sub(r"\n{4,}", "\n\n\n", s)
    s = re.sub(
        r"(?im)^\s*(?:важно отметить,?\s*|стоит отметить,?\s*|в современном мире\s*)",
        "",
        s,
    )
    return s.strip("\n")


def run_postcraft_deslop(text: str) -> str:
    if not text:
        return text
    if not os.path.exists(POSTCRAFT_DESLOP):
        return built_in_cleanup(text)

    proc = subprocess.run(
        ["python3", POSTCRAFT_DESLOP],
        input=text,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.rstrip("\n")


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9а-яё]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "present"


def pick_keyword(title: str) -> str:
    t = title.strip()
    if not t:
        return ""

    for pat in [r"«([^»]{2,40})»", r'"([^"]{2,40})"', r"\*([^*]{2,40})\*"]:
        m = re.search(pat, t)
        if m:
            return m.group(1).strip()

    tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9+-]{2,}", t)
    for tok in tokens:
        if re.fullmatch(r"[A-Z0-9+-]{3,}", tok):
            return tok
    for tok in reversed(tokens):
        if len(tok) >= 4:
            return tok
    return tokens[-1] if tokens else ""


def title_html(title: str) -> str:
    kw = pick_keyword(title)
    esc = html.escape(title)
    if not kw:
        return esc
    kw_esc = html.escape(kw)
    return esc.replace(kw_esc, f"<span class='title-accent'>{kw_esc}</span>", 1)


def detect_mode(title: str, raw: str) -> str:
    blob = f"{title} {raw}".lower()
    if any(h in blob for h in REPORT_HINTS):
        return "report"
    if any(h in blob for h in OFFER_HINTS):
        return "offer"
    return "general"


def split_sections(raw: str):
    normalized = (raw or "").replace("\r\n", "\n")
    normalized = re.sub(r"^\s*#\s+.+\n", "", normalized, count=1)
    sec = [s.strip() for s in re.split(r"\n(?=##\s+)", normalized) if s.strip()]
    return sec or [f"## Содержание\n{normalized.strip() or 'Пусто'}"]


def parse_header(h: str):
    if "|" in h:
        left, right = h.split("|", 1)
        return left.strip(), right.strip()
    return h.strip(), ""


def parse_table(lines):
    rows = []
    for ln in lines:
        s = ln.strip()
        if not s.startswith("|"):
            return None
        rows.append([c.strip() for c in s.strip("|").split("|")])
    return rows if len(rows) >= 2 else None


def render_table(rows):
    head = rows[0]
    separator = len(rows) >= 2 and rows[1] and set(rows[1][0]).issubset({"-", ":"})
    body = rows[2:] if separator else rows[1:]
    th = "".join(f"<th>{rich(c)}</th>" for c in head)
    tr = "".join("<tr>" + "".join(f"<td>{rich(c)}</td>" for c in r) + "</tr>" for r in body)
    return f"<div class='table-wrap'><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>"


def row2(line: str):
    return [p.strip() for p in line.split("::")]


def render_flow(items):
    nodes = []
    for idx, (title, body, emoji) in enumerate(items):
        connector = ""
        if idx < len(items) - 1:
            connector = (
                "<div style='display:flex;align-items:center;justify-content:center;min-width:54px;"
                "color:#8b90ff;font-size:24px;font-weight:900;opacity:.9;padding:0 4px;'>→</div>"
            )
        nodes.append(
            "<div style='display:flex;align-items:stretch;gap:0;flex:1 1 220px;min-width:220px;'>"
            "<div style='background:linear-gradient(135deg, rgba(99,102,241,.18), rgba(139,92,246,.12));"
            "border:1px solid rgba(129,140,248,.28);border-radius:18px;padding:16px 16px 14px;"
            "box-shadow:0 10px 28px rgba(0,0,0,.18);width:100%;'>"
            f"<div style='font-size:24px;line-height:1;margin-bottom:10px;'>{html.escape(emoji)}</div>"
            f"<div style='font-size:.95rem;font-weight:800;color:#fff;margin-bottom:8px;'>{rich(title)}</div>"
            f"<div style='color:#d4d4d8;line-height:1.55;font-size:.92rem;'>{rich(body)}</div>"
            "</div></div>"
            f"{connector}"
        )
    return (
        "<div style='margin:14px 0 8px;'>"
        "<div style='display:flex;flex-wrap:wrap;align-items:stretch;gap:10px;'>"
        + "".join(nodes)
        + "</div></div>"
    )


def render_beforeafter(items):
    blocks = []
    for before_title, before_body, after_title, after_body in items:
        blocks.append(
            "<div style='display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);gap:12px;align-items:stretch;margin:12px 0;'>"
            "<div style='background:rgba(239,68,68,.06);border:1px solid rgba(239,68,68,.24);border-radius:16px;padding:16px;'>"
            f"<div style='font-size:.78rem;letter-spacing:.04em;text-transform:uppercase;color:#fca5a5;font-weight:800;margin-bottom:8px;'>Before · {rich(before_title)}</div>"
            f"<div style='color:#e5e7eb;line-height:1.6;'>{rich(before_body)}</div>"
            "</div>"
            "<div style='display:flex;align-items:center;justify-content:center;color:#8b90ff;font-size:28px;font-weight:900;padding:0 2px;'>→</div>"
            "<div style='background:rgba(34,197,94,.06);border:1px solid rgba(34,197,94,.24);border-radius:16px;padding:16px;'>"
            f"<div style='font-size:.78rem;letter-spacing:.04em;text-transform:uppercase;color:#86efac;font-weight:800;margin-bottom:8px;'>After · {rich(after_title)}</div>"
            f"<div style='color:#e5e7eb;line-height:1.6;'>{rich(after_body)}</div>"
            "</div></div>"
        )
    return "".join(blocks)


def parse_chart_value(raw_value: str) -> float:
    s = str(raw_value or '').strip().replace(',', '.')
    if not s:
        return 0.0
    if '/' in s:
        left, right = s.split('/', 1)
        try:
            num = float(left.strip())
            den = float(right.strip().replace('%', ''))
            if den > 0:
                return (num / den) * 100.0
        except ValueError:
            return 0.0
    try:
        return float(s.replace('%', '').strip())
    except ValueError:
        return 0.0


def render_chart(items):
    nums = [parse_chart_value(value) for _, value, _ in items]
    max_value = max(nums) if nums else 1.0
    if max_value <= 0:
        max_value = 1.0

    rows = []
    for idx, (label, value, note) in enumerate(items):
        width = max(8.0, round((nums[idx] / max_value) * 100, 2))
        rows.append(
            "<div style='display:grid;grid-template-columns:minmax(110px,180px) minmax(0,1fr) auto;gap:12px;align-items:center;margin:10px 0;'>"
            f"<div style='color:#fff;font-weight:700;line-height:1.35;'>{rich(label)}</div>"
            "<div style='background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.08);border-radius:999px;height:12px;overflow:hidden;'>"
            f"<div style='height:100%;width:{width}%;background:linear-gradient(135deg,#6366f1,#8b90ff,#22d3ee);border-radius:999px;box-shadow:0 0 18px rgba(99,102,241,.26);'></div>"
            "</div>"
            f"<div style='text-align:right;white-space:nowrap;color:#e5e7eb;font-weight:800;'>{rich(value)}</div>"
            "</div>"
            + (f"<div style='margin:-4px 0 6px;color:#a1a1aa;font-size:.88rem;line-height:1.5;'>{rich(note)}</div>" if note else "")
        )
    return (
        "<div style='background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.10);border-radius:18px;padding:16px 16px 10px;margin:12px 0;'>"
        + "".join(rows)
        + "</div>"
    )


def render_lines(lines):
    blocks = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        if SEP_RE.match(s):
            i += 1
            continue

        hm = HEADING_RE.match(s)
        if hm:
            level = min(6, len(hm.group(1)) + 1)
            blocks.append(f"<h{level}>{rich(hm.group(2).strip())}</h{level}>")
            i += 1
            continue

        if s.startswith("@quote "):
            blocks.append(f"<div class='quote'>{rich(s[7:].strip())}</div>")
            i += 1
            continue

        if s.startswith("@meta "):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("@meta "):
                p = row2(lines[i].strip()[6:])
                if len(p) >= 2:
                    rows.append((p[0], p[1]))
                i += 1
            if rows:
                html_rows = "".join(
                    f"<div class='meta-row'><span class='meta-key'>{rich(k)}</span><span class='meta-val'>{rich(v)}</span></div>"
                    for k, v in rows
                )
                blocks.append(f"<div class='meta-block'>{html_rows}</div>")
            continue

        if s.startswith("@kpi "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("@kpi "):
                p = row2(lines[i].strip()[5:])
                if len(p) >= 2:
                    items.append((p[0], p[1]))
                i += 1
            if items:
                cards = "".join(
                    f"<div class='kpi'><div class='label'>{rich(a)}</div><div class='value'>{rich(b)}</div></div>"
                    for a, b in items
                )
                cls = "grid-3" if len(items) >= 3 else "grid-2"
                blocks.append(f"<div class='{cls}'>{cards}</div>")
            continue

        simple_boxes = [
            ("@question ", "question", "❓"),
            ("@note ", "note", "📝"),
            ("@warning ", "warning", "⚠️"),
        ]
        matched_box = False
        for prefix, cls, icon in simple_boxes:
            if s.startswith(prefix):
                blocks.append(f"<div class='{cls}'><span class='q-icon'>{icon}</span><div>{rich(s[len(prefix):].strip())}</div></div>")
                i += 1
                matched_box = True
                break
        if matched_box:
            continue

        if s.startswith("@verdict "):
            p = row2(s[9:].strip())
            title = rich(p[0]) if p else "Вердикт"
            desc = rich(p[1]) if len(p) > 1 else ""
            blocks.append(f"<div class='verdict'><div class='verdict-title'>🏁 {title}</div><div class='verdict-body'>{desc}</div></div>")
            i += 1
            continue

        if s.startswith("@risk "):
            p = row2(s[6:].strip())
            title = rich(p[0]) if p else "Риск"
            desc = rich(p[1]) if len(p) > 1 else ""
            blocks.append(f"<div class='risk'><strong>{title}</strong><div>{desc}</div></div>")
            i += 1
            continue

        if s.startswith("@check "):
            blocks.append(f"<div class='check'>✅ {rich(s[7:].strip())}</div>")
            i += 1
            continue

        if s.startswith("@take "):
            blocks.append(f"<div class='take'>🔥 {rich(s[6:].strip())}</div>")
            i += 1
            continue

        if s.startswith("@avoid "):
            blocks.append(f"<div class='avoid'>🚫 {rich(s[7:].strip())}</div>")
            i += 1
            continue

        if s.startswith("@phase "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("@phase "):
                p = row2(lines[i].strip()[7:])
                if len(p) >= 2:
                    items.append((p[0], p[1], p[2] if len(p) > 2 else "🗺️"))
                i += 1
            cards = "".join(
                f"<div class='phase-card'><div class='phase-emoji'>{html.escape(e)}</div><div class='phase-title'>{rich(t)}</div><div class='phase-desc'>{rich(d)}</div></div>"
                for t, d, e in items
            )
            cls = "grid-3" if len(items) >= 3 else "grid-2"
            blocks.append(f"<div class='{cls}'>{cards}</div>")
            continue

        if s.startswith("@flow "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("@flow "):
                p = row2(lines[i].strip()[6:])
                if len(p) >= 2:
                    items.append((p[0], p[1], p[2] if len(p) > 2 else "⚡"))
                i += 1
            blocks.append(render_flow(items))
            continue

        if s.startswith("@compare "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("@compare "):
                p = row2(lines[i].strip()[9:])
                if len(p) >= 4:
                    items.append((p[0], p[1], p[2], p[3]))
                i += 1
            html_items = "".join(
                "<div class='compare'>"
                + f"<div class='compare-side'><div class='compare-label'>✨ {rich(a)}</div><div class='compare-body'>{rich(b)}</div></div>"
                + f"<div class='compare-side alt'><div class='compare-label'>⚡ {rich(c)}</div><div class='compare-body'>{rich(d)}</div></div>"
                + "</div>"
                for a, b, c, d in items
            )
            blocks.append(html_items)
            continue

        if s.startswith("@decision "):
            p = row2(s[10:].strip())
            title = rich(p[0]) if p else "Decision"
            answer = rich(p[1]) if len(p) > 1 else ""
            why = rich(p[2]) if len(p) > 2 else ""
            blocks.append(
                "<div class='decision'>"
                + f"<div class='decision-title'>🧠 {title}</div>"
                + f"<div class='decision-answer'>{answer}</div>"
                + (f"<div class='decision-why'>{why}</div>" if why else "")
                + "</div>"
            )
            i += 1
            continue

        if s.startswith("@beforeafter "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("@beforeafter "):
                p = row2(lines[i].strip()[13:])
                if len(p) >= 4:
                    items.append((p[0], p[1], p[2], p[3]))
                i += 1
            blocks.append(render_beforeafter(items))
            continue

        if s.startswith("@chart "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("@chart "):
                p = row2(lines[i].strip()[7:])
                if len(p) >= 2:
                    items.append((p[0], p[1], p[2] if len(p) > 2 else ""))
                i += 1
            blocks.append(render_chart(items))
            continue

        if s.startswith("@timeline "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("@timeline "):
                p = row2(lines[i].strip()[10:])
                if len(p) >= 2:
                    items.append((p[0], p[1], p[2] if len(p) > 2 else "⏱️"))
                i += 1
            html_items = "".join(
                f"<div class='timeline-item'><div class='timeline-node'>{html.escape(e)}</div><div class='timeline-content'><div class='timeline-label'>{rich(l)}</div><div class='timeline-body'>{rich(b)}</div></div></div>"
                for l, b, e in items
            )
            blocks.append(f"<div class='timeline'>{html_items}</div>")
            continue

        if s.startswith("@stack "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("@stack "):
                p = row2(lines[i].strip()[7:])
                if len(p) >= 2:
                    items.append((p[0], p[1], p[2] if len(p) > 2 else "🧱"))
                i += 1
            html_items = "".join(
                f"<div class='stack-item'><div class='stack-emoji'>{html.escape(e)}</div><div class='stack-main'><div class='stack-title'>{rich(t)}</div><div class='stack-body'>{rich(b)}</div></div></div>"
                for t, b, e in items
            )
            blocks.append(f"<div class='stack'>{html_items}</div>")
            continue

        if s.startswith("@entity "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("@entity "):
                p = row2(lines[i].strip()[8:])
                if len(p) >= 2:
                    items.append((p[0], p[1], p[2] if len(p) > 2 else "📦"))
                i += 1
            cards = "".join(
                f"<div class='entity-card'><div class='entity-emoji'>{html.escape(e)}</div><div class='entity-content'><div class='entity-title'>{rich(t)}</div><div class='entity-body'>{rich(b)}</div></div></div>"
                for t, b, e in items
            )
            cls = "grid-3" if len(items) >= 3 else "grid-2"
            blocks.append(f"<div class='{cls}'>{cards}</div>")
            continue

        if s.startswith("@infra "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("@infra "):
                p = row2(lines[i].strip()[7:])
                if len(p) >= 3:
                    items.append((p[0], p[1], p[2]))
                i += 1
            cards = "".join(
                f"<div class='infra-item'><div class='meta-key'>{rich(a)}</div><div class='meta-val'>{rich(b)}</div><div class='meta-key'>{rich(c)}</div></div>"
                for a, b, c in items
            )
            cls = "grid-2" if len(items) <= 2 else "grid-3"
            blocks.append(f"<div class='{cls}'>{cards}</div>")
            continue

        if s.startswith("@team "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("@team "):
                p = row2(lines[i].strip()[6:])
                if len(p) >= 2:
                    items.append((p[0], p[1], p[2] if len(p) > 2 else "👤"))
                i += 1
            cards = "".join(
                f"<div class='team-item'><div>{html.escape(e)}</div><div>{rich(r)}</div><div class='team-count'>{rich(c)}</div></div>"
                for r, c, e in items
            )
            cls = "grid-3" if len(items) >= 3 else "grid-2"
            blocks.append(f"<div class='{cls}'>{cards}</div>")
            continue

        if s.startswith("```"):
            j = i + 1
            code = []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                code.append(lines[j])
                j += 1
            blocks.append(f"<pre><code>{html.escape(chr(10).join(code))}</code></pre>")
            i = j + 1 if j < len(lines) else j
            continue

        if s.startswith("|"):
            j = i
            t_lines = []
            while j < len(lines) and lines[j].strip().startswith("|"):
                t_lines.append(lines[j])
                j += 1
            table = parse_table(t_lines)
            if table:
                blocks.append(render_table(table))
                i = j
                continue

        if UNORDERED_RE.match(s):
            j = i
            items = []
            while j < len(lines) and UNORDERED_RE.match(lines[j].strip()):
                items.append(rich(UNORDERED_RE.sub("", lines[j].strip(), count=1)))
                j += 1
            blocks.append("<ul class='clean'>" + "".join(f"<li>{it}</li>" for it in items) + "</ul>")
            i = j
            continue

        if ORDERED_RE.match(s):
            j = i
            items = []
            while j < len(lines) and ORDERED_RE.match(lines[j].strip()):
                items.append(rich(ORDERED_RE.sub("", lines[j].strip(), count=1)))
                j += 1
            blocks.append("<ol class='clean ordered'>" + "".join(f"<li>{it}</li>" for it in items) + "</ol>")
            i = j
            continue

        j = i
        p = []
        while j < len(lines):
            t = lines[j].strip()
            if (
                not t
                or t.startswith("@")
                or t.startswith("|")
                or t.startswith("```")
                or UNORDERED_RE.match(t)
                or ORDERED_RE.match(t)
                or HEADING_RE.match(t)
                or SEP_RE.match(t)
            ):
                break
            p.append(rich(t))
            j += 1
        blocks.append(f"<p>{' '.join(p)}</p>")
        i = j

    return "".join(blocks)


def render_section(section: str, idx: int) -> str:
    lines = section.splitlines()
    title = "Секция"
    meta = ""
    body = lines
    if lines and lines[0].startswith("## "):
        title, meta = parse_header(lines[0][3:])
        body = lines[1:]
    clean_title = re.sub(r"^\s*\d+[\)\.]\s*", "", title).strip() or title
    body_html = render_lines(body)
    meta_html = f"<span class='meta'>{html.escape(meta)}</span>" if meta else ""
    head = f"<div class='card-header'><div class='step'>{idx}</div><h2>{html.escape(clean_title)}</h2>{meta_html}</div>"
    return f"<section class='card'>{head}{body_html}</section>"


def render_slide(section: str, idx: int) -> str:
    lines = section.splitlines()
    title = "Секция"
    meta = ""
    body = lines
    if lines and lines[0].startswith("## "):
        title, meta = parse_header(lines[0][3:])
        body = lines[1:]

    clean_title = re.sub(r"^\s*\d+[\)\.]\s*", "", title).strip() or title
    body_html = render_lines(body)
    kicker = meta.strip() or f"Слайд {idx + 1}"

    return (
        f"<section class='slide content-slide' data-slide='{idx + 1}' id='slide-{idx + 1}'>"
        "<div class='panel'>"
        f"<div class='kicker'>{html.escape(kicker)}</div>"
        f"<h2>{html.escape(clean_title)}</h2>"
        f"<div class='slide-body'>{body_html}</div>"
        "</div></section>"
    )


def md_to_blocks(raw: str) -> str:
    return "\n".join(render_section(s, i + 1) for i, s in enumerate(split_sections(raw)))


def md_to_slides(raw: str, title: str, subtitle: str, badge: str):
    sections = split_sections(raw)
    slides = [
        "<section class='slide title-slide' data-slide='1' id='slide-1'>"
        "<div class='panel'>"
        f"<div class='kicker'>{html.escape(badge)}</div>"
        f"<h1>{title_html(title)}</h1>"
        + (f"<p class='subtitle'>{html.escape(subtitle)}</p>" if subtitle.strip() else "")
        + "</div></section>"
    ]
    slides.extend(render_slide(section, idx + 1) for idx, section in enumerate(sections))
    dots = "".join(
        f"<button class='dot' type='button' aria-label='Перейти к слайду {i + 1}' data-slide='{i + 1}'></button>"
        for i in range(len(slides))
    )
    return "\n".join(slides), dots


def infer_badge(title: str, raw: str, mode: str) -> str:
    blob = f"{title}\n{raw}".lower()

    strict_rules = [
        (("enterprise", "компан"), "Enterprise AI"),
    ]
    for needles, label in strict_rules:
        if all(n in blob for n in needles):
            return label

    rules = [
        (("архитект", "architecture", "control plane", "runtime"), "Architecture"),
        (("strategy", "стратег", "позиционир", "positioning"), "Strategy"),
        (("go to market", "gtm", "рынок", "market"), "Go-To-Market"),
        (("оффер", "offer", "proposal", "deal", "переговор"), "Offer"),
        (("report", "отч", "аналит", "исслед", "kpi"), "Report"),
        (("roadmap", "phase", "этап", "rollout"), "Roadmap"),
        (("workflow", "handoff", "process", "flow", "воронк"), "Workflow"),
        (("memory", "памят", "knowledge"), "Knowledge"),
        (("agent", "agents", "агент"), "Agent System"),
    ]
    for needles, label in rules:
        if any(n in blob for n in needles):
            return label

    fallback = {
        "report": "Report",
        "offer": "Offer",
        "h20": "Human 2.0",
        "general": "Brief",
        "auto": "Brief",
    }.get(mode, "Brief")

    cleaned = re.sub(r"\b(human20|human 2\.0|для|про|как|что|зачем|почему|человеческим|языком)\b", " ", title, flags=re.I)
    tokens = [t for t in re.findall(r"[A-Za-zА-Яа-яЁё0-9+-]{3,}", cleaned) if len(t) >= 4]
    if tokens:
        candidate = " ".join(tokens[:2]).strip()
        if 4 <= len(candidate) <= 18:
            return candidate.title()
    return fallback


def resolve_badge(raw_badge: str, title: str, raw: str, mode: str) -> str:
    b = (raw_badge or "").strip()
    if b and b != "/present":
        return b
    return infer_badge(title, raw, mode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="auto", choices=["auto", "general", "report", "offer", "h20", "slides"])
    ap.add_argument("--title", required=True)
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--badge", default="/present")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default="")
    args = ap.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        raw = f.read()

    raw = run_postcraft_deslop(raw)
    clean_title = run_postcraft_deslop(args.title)
    clean_subtitle = run_postcraft_deslop(args.subtitle)
    clean_badge_arg = run_postcraft_deslop(args.badge) if args.badge and args.badge != "/present" else args.badge

    mode = detect_mode(clean_title, raw) if args.mode == "auto" else args.mode
    badge = resolve_badge(clean_badge_arg, clean_title, raw, mode)
    with open(TEMPLATES[mode], "r", encoding="utf-8") as f:
        tpl = f.read()

    if mode == "slides":
        slides_html, dots_html = md_to_slides(raw, clean_title, clean_subtitle, badge)
        out_html = (
            tpl.replace("{{TITLE}}", title_html(clean_title))
            .replace("{{TITLE_TEXT}}", html.escape(clean_title))
            .replace("{{SUBTITLE}}", html.escape(clean_subtitle))
            .replace("{{BADGE}}", html.escape(badge))
            .replace("{{SLIDES}}", slides_html)
            .replace("{{DOTS}}", dots_html)
            .replace("{{FOOTER}}", f"Generated by /present ({mode}) · {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        )
    else:
        out_html = (
            tpl.replace("{{TITLE}}", title_html(clean_title))
            .replace("{{TITLE_TEXT}}", html.escape(clean_title))
            .replace("{{SUBTITLE}}", html.escape(clean_subtitle))
            .replace("{{BADGE}}", html.escape(badge))
            .replace("{{CONTENT_BLOCKS}}", md_to_blocks(raw))
            .replace("{{FOOTER}}", f"Generated by /present ({mode}) · {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        )

    out = args.output or os.path.abspath(f"present_{slugify(clean_title)}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(out_html)
    print(out)


if __name__ == "__main__":
    main()
