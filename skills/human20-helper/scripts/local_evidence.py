#!/usr/bin/env python3
import json
from pathlib import Path
from typing import Dict, List

WORKSPACE = Path('/home/assistent/.openclaw/workspace')
CONFIG = Path('/home/assistent/.openclaw/openclaw.json')

EVIDENCE_EXPLAINERS: Dict[str, Dict[str, str]] = {
    'portfolio_project_exists': {'label': 'Найден проект portfolio-site', 'source': 'workspace/projects'},
    'portfolio_git_repo': {'label': 'У проекта есть git-репозиторий', 'source': 'workspace/git'},
    'portfolio_site_files': {'label': 'Найдены основные файлы сайта', 'source': 'workspace/files'},
    'portfolio_git_remote': {'label': 'Найден git remote проекта', 'source': 'workspace/git'},
    'portfolio_vercel_traces': {'label': 'Есть следы деплоя/Vercel', 'source': 'workspace/files'},
    'portfolio_live_demo_link': {'label': 'Есть публичная ссылка на сайт', 'source': 'workspace/files'},
    'portfolio_chat_api': {'label': 'Найден chat API сайта', 'source': 'workspace/files'},
    'minimax_configured': {'label': 'MiniMax настроен в конфиге', 'source': 'openclaw/config'},
    'agents_md_exists': {'label': 'Файл AGENTS.md найден', 'source': 'workspace/files'},
    'webd_skill_exists': {'label': 'Навык webd найден', 'source': 'workspace/skills'},
    'openclaw_config_exists': {'label': 'Конфиг OpenClaw найден', 'source': 'openclaw/config'},
    'telegram_enabled': {'label': 'Telegram-канал включён', 'source': 'openclaw/config'},
    'persona_files_exist': {'label': 'Persona-файлы присутствуют', 'source': 'workspace/files'},
    'security_skill_exists': {'label': 'Навык security найден', 'source': 'workspace/skills'},
    'tg_skill_exists': {'label': 'Навык tg найден', 'source': 'workspace/skills'},
    'digest_skill_exists': {'label': 'Навык digest найден', 'source': 'workspace/skills'},
    'create_skills_exists': {'label': 'Навык create-skills найден', 'source': 'workspace/skills'},
    'tgpublish_exists': {'label': 'Навык tgpublish найден', 'source': 'workspace/skills'},
    'chro_skill_exists': {'label': 'Навык chro найден', 'source': 'workspace/skills'},
    'plan_times_exists': {'label': 'Навык plan-times найден', 'source': 'workspace/skills'},
    'portfolio_mentions': {'label': 'Есть упоминания портфолио в памяти', 'source': 'memory'},
    'google_oauth_memory': {'label': 'Есть следы Google OAuth/Calendar в памяти', 'source': 'memory'},
    'digest_cron_memory': {'label': 'Есть следы digest/cron контура в памяти', 'source': 'memory'},
    'telegram_publish_memory': {'label': 'Есть следы publish-контура в памяти', 'source': 'memory'},
}


def load_config() -> Dict:
    if not CONFIG.exists():
        return {}
    return json.loads(CONFIG.read_text())


def evidence_flags() -> Dict[str, bool]:
    cfg = load_config()
    skills_dir = WORKSPACE / 'skills'
    portfolio = WORKSPACE / 'ai-projects' / 'portfolio-site'

    flags: Dict[str, bool] = {}
    flags['portfolio_project_exists'] = portfolio.exists()
    flags['portfolio_git_repo'] = (portfolio / '.git').exists()
    flags['portfolio_site_files'] = (portfolio / 'index.html').exists()
    flags['portfolio_git_remote'] = (portfolio / '.git' / 'config').exists() and 'github.com:web3blind/portfolio-site.git' in (portfolio / '.git' / 'config').read_text(errors='ignore')
    flags['portfolio_vercel_traces'] = (portfolio / 'deploy.md').exists() and 'api.vercel.com' in (portfolio / 'deploy.md').read_text(errors='ignore')
    flags['portfolio_live_demo_link'] = (portfolio / 'index.html').exists() and 'vercel.app' in (portfolio / 'index.html').read_text(errors='ignore')
    flags['portfolio_chat_api'] = (portfolio / 'api' / 'chat.js').exists()

    models = (((cfg.get('models') or {}).get('providers') or {}))
    flags['minimax_configured'] = 'minimax-portal' in models
    flags['agents_md_exists'] = (WORKSPACE / 'AGENTS.md').exists()
    flags['webd_skill_exists'] = (skills_dir / 'webd' / 'SKILL.md').exists()

    flags['openclaw_config_exists'] = CONFIG.exists()
    flags['telegram_enabled'] = bool((((cfg.get('channels') or {}).get('telegram') or {}).get('enabled')))
    flags['persona_files_exist'] = all((WORKSPACE / p).exists() for p in ['SOUL.md', 'USER.md', 'AGENTS.md', 'MEMORY.md'])

    flags['security_skill_exists'] = (skills_dir / 'security' / 'SKILL.md').exists()
    flags['tg_skill_exists'] = (skills_dir / 'tg' / 'SKILL.md').exists()
    flags['digest_skill_exists'] = (skills_dir / 'digest' / 'SKILL.md').exists()
    flags['create_skills_exists'] = (skills_dir / 'create-skills' / 'SKILL.md').exists()
    flags['tgpublish_exists'] = (skills_dir / 'tgpublish' / 'SKILL.md').exists()

    flags['chro_skill_exists'] = (skills_dir / 'chro' / 'SKILL.md').exists()
    flags['plan_times_exists'] = (skills_dir / 'plan-times' / 'SKILL.md').exists()

    memory_texts: List[str] = []
    for path in [WORKSPACE / 'MEMORY.md', *(WORKSPACE / 'memory').glob('*.md')]:
        if path.exists():
            try:
                memory_texts.append(path.read_text(errors='ignore'))
            except Exception:
                pass
    memory_blob = '\n'.join(memory_texts)
    flags['portfolio_mentions'] = 'portfolio-site' in memory_blob or 'портфолио' in memory_blob.lower()
    flags['google_oauth_memory'] = 'Google OAuth' in memory_blob or 'Google Calendar' in memory_blob or 'consent' in memory_blob
    flags['digest_cron_memory'] = 'tg-digest-morning' in memory_blob or 'tg-digest-evening' in memory_blob or 'cron' in memory_blob and 'digest' in memory_blob
    flags['telegram_publish_memory'] = 'tgpublish' in memory_blob or 'Telegram publishing skill' in memory_blob
    return flags


def describe_flag(flag_key: str) -> Dict[str, str]:
    return EVIDENCE_EXPLAINERS.get(flag_key, {'label': flag_key, 'source': 'unknown'})


def build_evidence_records(keys: List[str], flags: Dict[str, bool], kind: str) -> List[Dict[str, object]]:
    records: List[Dict[str, object]] = []
    for key in keys:
        meta = describe_flag(key)
        records.append({
            'key': key,
            'kind': kind,
            'matched': bool(flags.get(key, False)),
            'label': meta['label'],
            'source': meta['source'],
        })
    return records


def evaluate(rules_path: Path) -> Dict:
    rules = json.loads(rules_path.read_text())['lessons']
    flags = evidence_flags()
    out = {'flags': flags, 'lessons': []}
    for lesson in rules:
        required = lesson.get('requiredEvidence', [])
        soft = lesson.get('softEvidence', [])
        required_records = build_evidence_records(required, flags, 'required')
        soft_records = build_evidence_records(soft, flags, 'soft')
        required_ok = all(flags.get(k, False) for k in required)
        soft_count = sum(1 for k in soft if flags.get(k, False))
        if required_ok:
            status = 'auto-pass'
        elif any(flags.get(k, False) for k in required) or soft_count > 0:
            status = 'soft-pass'
        else:
            status = 'manual' if lesson.get('fallbackQuestions') else 'fail'
        out['lessons'].append({
            'id': lesson['id'],
            'title': lesson['title'],
            'status': status,
            'requiredMatched': [k for k in required if flags.get(k, False)],
            'missingRequired': [k for k in required if not flags.get(k, False)],
            'softMatched': [k for k in soft if flags.get(k, False)],
            'requiredEvidenceVerdicts': required_records,
            'softEvidenceVerdicts': soft_records,
            'evidenceSummary': {
                'requiredTotal': len(required_records),
                'requiredMatched': sum(1 for item in required_records if item['matched']),
                'softTotal': len(soft_records),
                'softMatched': sum(1 for item in soft_records if item['matched']),
            },
            'nextStep': lesson.get('nextStep'),
            'fallbackQuestions': lesson.get('fallbackQuestions', []),
        })
    return out


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    result = evaluate(root / 'data' / 'lesson_rules.json')
    print(json.dumps(result, ensure_ascii=False, indent=2))
