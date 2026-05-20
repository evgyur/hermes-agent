#!/usr/bin/env python3
import argparse
import html
import json
import os
from datetime import datetime


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(ROOT, "templates", "present-visual-map.html")


def load_template():
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        return f.read()


def tg_link(username: str) -> str:
    if not username or not username.startswith("@"):
        return html.escape(username or "")
    u = html.escape(username)
    return f'<a href="https://t.me/{html.escape(username[1:])}" target="_blank" rel="noopener noreferrer">{u}</a>'


def render_summary_cards(spec: dict) -> str:
    hosts = spec.get("hosts", [])
    bot_count = sum(len(h.get("services", [])) for h in hosts)
    runtime_kinds = sorted({svc.get("runtime", "") for h in hosts for svc in h.get("services", []) if svc.get("runtime")})
    vals = [
        ("hosts", str(len(hosts))),
        ("services", str(bot_count)),
        ("runtimes", ", ".join(runtime_kinds) or "n/a"),
        ("focus", html.escape(spec.get("focus", "bot topology"))),
    ]
    return "".join(
        f"<div class='summary-card'><div class='label'>{html.escape(label)}</div><div class='value'>{value}</div></div>"
        for label, value in vals
    )


def render_notes(spec: dict) -> str:
    notes = spec.get("notes", [])
    return "".join(
        f"<div class='note'><strong>{html.escape(item.get('title', 'Note'))}</strong><div>{html.escape(item.get('body', ''))}</div></div>"
        for item in notes
    )


def render_host_cards(spec: dict) -> str:
    parts = []
    for host in spec.get("hosts", []):
        chips = []
        for tag in host.get("tags", []):
            chips.append(f"<span class='chip'>{html.escape(tag)}</span>")
        service_html = []
        for svc in host.get("services", []):
            title = html.escape(svc.get("name", "service"))
            username = svc.get("username")
            if username:
                title = f"{title} · {tg_link(username)}"
            service_html.append(
                "<div class='service'>"
                f"<div class='service-title'>{title}</div>"
                f"<div class='service-body'>{html.escape(svc.get('body', ''))}</div>"
                "</div>"
            )
        parts.append(
            "<article class='host-card'>"
            f"<h3>{html.escape(host.get('name', 'host'))}</h3>"
            f"<div class='host-meta'>{html.escape(host.get('meta', ''))}</div>"
            f"<div class='chips'>{''.join(chips)}</div>"
            f"<div class='services'>{''.join(service_html)}</div>"
            "</article>"
        )
    return "".join(parts)


def build_mermaid(spec: dict) -> str:
    lines = [
        "flowchart LR",
        "classDef host fill:#fff7ed,stroke:#b45309,color:#2e2418,stroke-width:2px;",
        "classDef user fill:#f0fdfa,stroke:#0f766e,color:#123a35;",
        "classDef bot fill:#fdf2f8,stroke:#9f1239,color:#4c122a;",
        "classDef infra fill:#f5f3ff,stroke:#6d28d9,color:#312e81;",
        "classDef issue fill:#fef2f2,stroke:#b91c1c,color:#7f1d1d,stroke-dasharray: 5 3;",
    ]
    for host in spec.get("hosts", []):
        hid = host["id"]
        lines.append(f"subgraph {hid}[\"{host['name']}\"]")
        lines.append(f"direction TB")
        lines.append(f"{hid}_host[\"{host['label']}\"]:::host")
        for user in host.get("users", []):
            uid = user["id"]
            lines.append(f"{uid}[\"{user['label']}\"]:::user")
            lines.append(f"{hid}_host --> {uid}")
        for svc in host.get("services", []):
            sid = svc["id"]
            label = svc["label"].replace('"', '\\"')
            klass = svc.get("class", "bot")
            parent = svc.get("parent") or f"{hid}_host"
            lines.append(f"{sid}[\"{label}\"]:::{klass}")
            lines.append(f"{parent} --> {sid}")
        lines.append("end")
    for edge in spec.get("edges", []):
        src = edge["from"]
        dst = edge["to"]
        label = edge.get("label")
        if label:
          lines.append(f"{src} -. \"{label}\" .-> {dst}")
        else:
          lines.append(f"{src} --> {dst}")
    return "\n".join(lines)


def render(spec: dict, title: str, subtitle: str) -> str:
    tpl = load_template()
    replacements = {
        "{{TITLE}}": html.escape(title),
        "{{SUBTITLE}}": html.escape(subtitle),
        "{{SUMMARY_CARDS}}": render_summary_cards(spec),
        "{{MERMAID}}": html.escape(build_mermaid(spec)),
        "{{NOTES}}": render_notes(spec),
        "{{HOST_CARDS}}": render_host_cards(spec),
        "{{TIMESTAMP}}": html.escape(datetime.now().strftime("%Y-%m-%d %H:%M")),
    }
    out = tpl
    for key, value in replacements.items():
        out = out.replace(key, value)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--subtitle", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        spec = json.load(f)

    html_out = render(spec, args.title, args.subtitle)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html_out)

    print(args.output)


if __name__ == "__main__":
    main()
