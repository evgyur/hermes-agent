#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from human20_mcp_client import Human20McpClient, Human20McpError
from local_evidence import evaluate

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / 'data' / 'lesson_rules.json'


def load_rules():
    return json.loads(RULES.read_text(encoding='utf-8'))['lessons']


def rules_map():
    return {item['id']: item for item in load_rules()}


def is_verified_for_sync(local_item):
    required_verdicts = local_item.get('requiredEvidenceVerdicts', [])
    return local_item.get('status') == 'auto-pass' and all(item.get('matched') for item in required_verdicts)


def _catalog_task_ids(catalog):
    if not isinstance(catalog, dict):
        return []
    tasks = catalog.get('tasks')
    if not isinstance(tasks, list):
        return []
    return [task.get('task_id') for task in tasks if isinstance(task, dict) and task.get('task_id')]


def _catalog_completed_task_ids(catalog):
    if not isinstance(catalog, dict):
        return []
    tasks = catalog.get('tasks')
    if not isinstance(tasks, list):
        return []
    return [
        task.get('task_id')
        for task in tasks
        if isinstance(task, dict) and task.get('task_id') and task.get('completed') is True
    ]


def fetch_homework_catalogs(client, local):
    catalogs = {}
    for lesson in local.get('lessons', []):
        lesson_id = lesson.get('id')
        if lesson_id:
            catalogs[lesson_id] = safe_call(client, 'get_homework_catalog', {'lesson_id': lesson_id})
    return catalogs


def _normalize_lesson_scope(lesson_ids=None):
    if lesson_ids is None:
        return None
    return {lesson_id for lesson_id in lesson_ids if lesson_id}


def build_auto_sync_plan(progress, homework_progress, local, homework_catalogs=None, lesson_ids=None):
    rule_map = rules_map()
    completed_items = set(progress.get('completedItems', [])) if isinstance(progress, dict) else set()
    homework_map = (homework_progress or {}).get('progress', {}) if isinstance(homework_progress, dict) else {}
    homework_catalogs = homework_catalogs or {}
    lesson_scope = _normalize_lesson_scope(lesson_ids)
    plan = []
    for lesson in local.get('lessons', []):
        lesson_id = lesson['id']
        if lesson_scope is not None and lesson_id not in lesson_scope:
            continue
        catalog = homework_catalogs.get(lesson_id)
        catalog_tasks = _catalog_task_ids(catalog)
        if catalog_tasks:
            expected_tasks = catalog_tasks
            completed_tasks = set(_catalog_completed_task_ids(catalog))
            mapping_source = 'get_homework_catalog'
        else:
            expected_tasks = rule_map.get(lesson_id, {}).get('homeworkTaskIds', [])
            completed_tasks = set(homework_map.get(lesson_id, []))
            mapping_source = 'lesson_rules.json'
        eligible = is_verified_for_sync(lesson)
        unexpected_live_tasks = sorted(task_id for task_id in completed_tasks if task_id not in expected_tasks)
        mapping_complete = bool(expected_tasks)
        sync_safe = eligible and mapping_complete and not unexpected_live_tasks
        missing_tasks = [task_id for task_id in expected_tasks if task_id not in completed_tasks]
        skip_reason = None
        if not eligible:
            skip_reason = 'not-verified'
        elif not mapping_complete:
            skip_reason = 'missing-homework-catalog'
        elif unexpected_live_tasks:
            skip_reason = 'unexpected-live-task-ids'
        plan.append({
            'lessonId': lesson_id,
            'eligible': eligible,
            'syncSafe': sync_safe,
            'skipReason': skip_reason,
            'mappingSource': mapping_source,
            'unexpectedLiveTaskIds': unexpected_live_tasks,
            'expectedHomeworkTaskIds': expected_tasks,
            'markComplete': sync_safe and lesson_id not in completed_items,
            'missingHomeworkTaskIds': missing_tasks if sync_safe else [],
            'state': lesson_state(lesson.get('status')),
        })
    return plan


def run_auto_sync(client, progress, homework_progress, local, homework_catalogs=None, lesson_ids=None):
    plan = build_auto_sync_plan(progress, homework_progress, local, homework_catalogs, lesson_ids=lesson_ids)
    applied = []
    skipped = []
    errors = []
    current_progress = progress
    current_homework = homework_progress

    for item in plan:
        if not item['syncSafe']:
            skipped.append({
                'lessonId': item['lessonId'],
                'reason': item.get('skipReason') or 'sync-not-safe',
                'unexpectedLiveTaskIds': item.get('unexpectedLiveTaskIds', []),
            })
            continue
        lesson_actions = []
        lesson_id = item['lessonId']

        if item['markComplete']:
            try:
                updated_progress = client.structured_tool('mark_complete', {'item_id': lesson_id})
                if lesson_id not in set(updated_progress.get('completedItems', [])):
                    raise Human20McpError(f'mark_complete did not persist for {lesson_id}')
                current_progress = updated_progress
                lesson_actions.append('mark_complete')
            except Exception as exc:
                errors.append({'lessonId': lesson_id, 'action': 'mark_complete', 'error': str(exc)})
                continue

        if item['missingHomeworkTaskIds']:
            for task_id in item['missingHomeworkTaskIds']:
                try:
                    updated_homework = client.structured_tool('toggle_homework_task', {'lesson_id': lesson_id, 'task_id': task_id})
                    completed_tasks = set((updated_homework.get('progress') or {}).get(lesson_id, []))
                    if task_id not in completed_tasks:
                        raise Human20McpError(f'toggle_homework_task did not add {task_id} for {lesson_id}')
                    current_homework = updated_homework
                    lesson_actions.append(f'toggle_homework_task:{task_id}')
                except Exception as exc:
                    errors.append({'lessonId': lesson_id, 'action': f'toggle_homework_task:{task_id}', 'error': str(exc)})
                    break

        if lesson_actions:
            applied.append({'lessonId': lesson_id, 'actions': lesson_actions})
        else:
            skipped.append({'lessonId': lesson_id, 'reason': 'already-synced'})

    return {
        'plan': plan,
        'applied': applied,
        'skipped': skipped,
        'errors': errors,
        'progress': current_progress,
        'homework': current_homework,
    }


def first_unfinished(local_lessons):
    for item in local_lessons:
        if item['status'] != 'auto-pass':
            return item
    return None


def lesson_state(status: str) -> str:
    mapping = {
        'auto-pass': 'verified',
        'soft-pass': 'needs_work',
        'manual': 'awaiting_confirmation',
        'fail': 'not_started',
    }
    return mapping.get(status, 'unknown')


def workshop_lesson_map(workshop):
    return {x['id']: x for x in workshop.get('lessons', [])}


def _homework_progress_map(homework_progress):
    if not isinstance(homework_progress, dict):
        return {}
    progress = homework_progress.get('progress')
    return progress if isinstance(progress, dict) else {}


def build_homework_summary(homework_progress, homework_catalogs=None):
    homework_map = _homework_progress_map(homework_progress)
    homework_catalogs = homework_catalogs or {}
    summary = []
    for rule in load_rules():
        lesson_id = rule['id']
        catalog = homework_catalogs.get(lesson_id)
        catalog_tasks = _catalog_task_ids(catalog)
        if catalog_tasks:
            expected_tasks = catalog_tasks
            completed_tasks = set(_catalog_completed_task_ids(catalog))
            mapping_source = 'get_homework_catalog'
        else:
            expected_tasks = rule.get('homeworkTaskIds', [])
            completed_tasks = set(homework_map.get(lesson_id, []))
            mapping_source = 'lesson_rules.json'
        completed_count = len([task_id for task_id in expected_tasks if task_id in completed_tasks])
        expected_count = len(expected_tasks)
        summary.append({
            'lessonId': lesson_id,
            'title': rule.get('title'),
            'completedCount': completed_count,
            'expectedCount': expected_count,
            'done': expected_count > 0 and completed_count >= expected_count,
            'mappingSource': mapping_source,
            'completedHomeworkTaskIds': [task_id for task_id in expected_tasks if task_id in completed_tasks],
            'missingHomeworkTaskIds': [task_id for task_id in expected_tasks if task_id not in completed_tasks],
        })
    return summary


def first_unfinished_homework(homework_lessons):
    for item in homework_lessons:
        if not item.get('done'):
            return item
    return None


def build_summary(workshop, progress, onboarding, digest, local, homework_progress=None, homework_catalogs=None):
    lessons = local['lessons']
    completed_items = progress.get('completedItems', []) if isinstance(progress, dict) else []
    homework_lessons = build_homework_summary(homework_progress, homework_catalogs)
    use_homework_progress = not completed_items and any(item.get('completedCount', 0) > 0 for item in homework_lessons)
    homework_next_lesson = first_unfinished_homework(homework_lessons) if use_homework_progress else None
    next_lesson = None if use_homework_progress else first_unfinished(lessons)
    lesson_map = workshop_lesson_map(workshop)
    rule_map = rules_map()
    next_lesson_id = homework_next_lesson['lessonId'] if homework_next_lesson else (next_lesson['id'] if next_lesson else None)
    href = lesson_map.get(next_lesson_id, {}).get('href') if next_lesson_id else None
    practical = rule_map.get(next_lesson_id, {}).get('practicalActions', []) if next_lesson_id else []
    fallback_questions = rule_map.get(next_lesson_id, {}).get('fallbackQuestions', []) if next_lesson_id else []
    next_step = rule_map.get(next_lesson_id, {}).get('nextStep') if next_lesson_id else None
    return {
        'mcp': {
            'completedItems': completed_items,
            'completedCount': onboarding.get('completedCount') or digest.get('completedCount'),
            'digestFocus': digest.get('focus'),
            'onboardingStatus': onboarding.get('status'),
        },
        'localLessons': lessons,
        'homeworkLessons': homework_lessons,
        'useHomeworkProgress': use_homework_progress,
        'nextLesson': {
            'id': next_lesson_id,
            'title': (homework_next_lesson.get('title') if homework_next_lesson else next_lesson['title']) if next_lesson_id else None,
            'state': 'needs_work' if homework_next_lesson else (lesson_state(next_lesson['status']) if next_lesson else 'done'),
            'href': href,
            'nextStep': next_step or (next_lesson['nextStep'] if next_lesson else 'Все основные уроки локально подтверждены.'),
            'practicalActions': practical,
            'fallbackQuestions': fallback_questions if homework_next_lesson else (next_lesson['fallbackQuestions'] if next_lesson else []),
        }
    }


def safe_call(client, tool_name, arguments=None):
    try:
        return client.extract_structured(client.call_tool(tool_name, arguments or {}))
    except Human20McpError as e:
        return {'error': str(e)}


def build_changed_since(client, since: str):
    return safe_call(client, 'get_changed_since', {'since': since})


def build_whats_new(client):
    return safe_call(client, 'get_whats_new')


def pick_recommended_for_lesson(digest, lesson_id):
    if not isinstance(digest, dict):
        return []
    candidates = digest.get('recommendedContent') or digest.get('contentToCatchUp') or []
    lesson_num = lesson_id.split('-')[-1] if lesson_id.startswith('lesson-') else lesson_id
    picked = []
    for item in candidates:
        text = json.dumps(item, ensure_ascii=False).lower()
        if lesson_id.lower() in text or f'урок {lesson_num}' in text or f'lesson-{lesson_num}' in text:
            picked.append(item)
    return picked[:3]


def build_continuation(lesson_id, workshop, digest, local):
    lesson_map = workshop_lesson_map(workshop)
    rule = rules_map().get(lesson_id)
    local_item = next((x for x in local['lessons'] if x['id'] == lesson_id), None)
    workshop_item = lesson_map.get(lesson_id, {})
    recommended = pick_recommended_for_lesson(digest, lesson_id)
    return {
        'lessonId': lesson_id,
        'title': (rule or {}).get('title') or workshop_item.get('title'),
        'href': workshop_item.get('href'),
        'localStatus': local_item.get('status') if local_item else 'unknown',
        'state': lesson_state(local_item.get('status')) if local_item else 'unknown',
        'requiredEvidenceVerdicts': local_item.get('requiredEvidenceVerdicts', []) if local_item else [],
        'softEvidenceVerdicts': local_item.get('softEvidenceVerdicts', []) if local_item else [],
        'nextStep': (rule or {}).get('nextStep'),
        'practicalActions': (rule or {}).get('practicalActions', []),
        'fallbackQuestions': (rule or {}).get('fallbackQuestions', []),
        'recommendedContent': recommended,
    }


def build_verify(lesson_id, workshop, local):
    lesson_map = workshop_lesson_map(workshop)
    local_item = next((x for x in local['lessons'] if x['id'] == lesson_id), None)
    workshop_item = lesson_map.get(lesson_id, {})
    if not local_item:
        return {
            'lessonId': lesson_id,
            'state': 'unknown',
            'title': workshop_item.get('title'),
            'href': workshop_item.get('href'),
            'matchedRequired': [],
            'missingRequired': [],
            'matchedSoft': [],
            'nextStep': None,
            'fallbackQuestions': ['Урок не найден в локальных правилах.'],
        }
    return {
        'lessonId': lesson_id,
        'state': lesson_state(local_item.get('status')),
        'title': local_item.get('title') or workshop_item.get('title'),
        'href': workshop_item.get('href'),
        'matchedRequired': [item for item in local_item.get('requiredEvidenceVerdicts', []) if item.get('matched')],
        'missingRequired': [item for item in local_item.get('requiredEvidenceVerdicts', []) if not item.get('matched')],
        'matchedSoft': [item for item in local_item.get('softEvidenceVerdicts', []) if item.get('matched')],
        'nextStep': local_item.get('nextStep'),
        'fallbackQuestions': local_item.get('fallbackQuestions', []),
    }


def build_next_action(summary):
    next_lesson = summary['nextLesson']
    if not next_lesson['id']:
        return {
            'state': 'done',
            'message': 'Все основные уроки локально подтверждены.',
            'lessonId': None,
            'actions': [
                'Выбери один урок и пройди пост-аудит по practical actions.',
                'Открой changed-since или digest и выбери слой для усиления.',
            ],
        }
    return {
        'state': next_lesson.get('state', 'unknown'),
        'message': f"Сейчас логичнее всего двигаться по {next_lesson['id']}.",
        'lessonId': next_lesson['id'],
        'title': next_lesson.get('title'),
        'href': next_lesson.get('href'),
        'actions': next_lesson.get('practicalActions', []),
        'nextStep': next_lesson.get('nextStep'),
    }


def build_human_output(summary):
    next_lesson = summary['nextLesson']
    lines = []
    if summary.get('useHomeworkProgress'):
        lines.append('Текущее состояние по урокам (по homework в Human20):')
        for lesson in summary.get('homeworkLessons', []):
            status_label = 'завершено' if lesson.get('done') else 'не завершено'
            lines.append(f"- {lesson['lessonId']}: {status_label} ({lesson.get('completedCount', 0)}/{lesson.get('expectedCount', 0)})")
        lines.append('')
        lines.append('Локальная проверка файлов и артефактов может отставать от факта завершения в Human20.')
        if next_lesson['id']:
            lines.append('')
            lines.append('Следующий этап:')
            lines.append(f"- {next_lesson['title']}")
            if next_lesson['href']:
                lines.append(f"- ссылка: {next_lesson['href']}")
            lines.append(f"- следующий шаг: {next_lesson['nextStep']}")
            if next_lesson['practicalActions']:
                lines.append('- что делать сейчас:')
                for action in next_lesson['practicalActions']:
                    lines.append(f"  - {action}")
        else:
            lines.append('')
            lines.append('Все основные уроки закрыты по homework в Human20.')
        return '\n'.join(lines)

    lines.append('Текущее состояние по урокам:')
    for lesson in summary['localLessons']:
        evidence = lesson.get('evidenceSummary', {})
        status_label = {
            'auto-pass': 'подтверждено',
            'soft-pass': 'частично подтверждено',
            'manual': 'нужно подтверждение',
            'fail': 'не подтверждено',
        }.get(lesson['status'], lesson['status'])
        details = []
        required_total = evidence.get('requiredTotal', 0)
        required_matched = evidence.get('requiredMatched', 0)
        soft_total = evidence.get('softTotal', 0)
        soft_matched = evidence.get('softMatched', 0)
        if required_total:
            details.append(f"required {required_matched}/{required_total}")
        if soft_total:
            details.append(f"soft {soft_matched}/{soft_total}")
        suffix = f" ({', '.join(details)})" if details else ''
        lines.append(f"- {lesson['id']}: {status_label}{suffix}")
    lines.append('')
    if next_lesson['id']:
        lesson_state = next((item for item in summary['localLessons'] if item['id'] == next_lesson['id']), None)
        lines.append('Следующий этап:')
        lines.append(f"- {next_lesson['title']}")
        if next_lesson['href']:
            lines.append(f"- ссылка: {next_lesson['href']}")
        if lesson_state:
            matched_required = [item['label'] for item in lesson_state.get('requiredEvidenceVerdicts', []) if item.get('matched')]
            missing_required = [item['label'] for item in lesson_state.get('requiredEvidenceVerdicts', []) if not item.get('matched')]
            matched_soft = [item['label'] for item in lesson_state.get('softEvidenceVerdicts', []) if item.get('matched')]
            if matched_required:
                lines.append('- уже подтверждено:')
                for item in matched_required:
                    lines.append(f"  - {item}")
            if matched_soft:
                lines.append('- косвенно видно:')
                for item in matched_soft:
                    lines.append(f"  - {item}")
            if missing_required:
                lines.append('- пока не найдено локально:')
                for item in missing_required:
                    lines.append(f"  - {item}")
        lines.append(f"- следующий шаг: {next_lesson['nextStep']}")
        if next_lesson['practicalActions']:
            lines.append('- что делать сейчас:')
            for action in next_lesson['practicalActions']:
                lines.append(f"  - {action}")
        if next_lesson['fallbackQuestions']:
            lines.append('- что уточнить:')
            for q in next_lesson['fallbackQuestions']:
                lines.append(f"  - {q}")
    else:
        lines.append('Все основные уроки локально подтверждены.')
        lines.append('Что можно делать дальше:')
        lines.append('- выбрать один урок и пройти пост-аудит по practical actions')
        lines.append('- открыть свежий digest/changed-since и выбрать новый слой для усиления')
        lines.append('- перейти к helper-режиму continuation по конкретной теме')
    return '\n'.join(lines)


def build_human_continuation(cont):
    lines = []
    lines.append(f"Continuation по {cont['lessonId']}:")
    if cont.get('title'):
        lines.append(f"- {cont['title']}")
    if cont.get('href'):
        lines.append(f"- ссылка: {cont['href']}")
    lines.append(f"- локальный статус: {cont.get('localStatus')}")
    lines.append(f"- состояние: {cont.get('state')}")
    if cont.get('nextStep'):
        lines.append(f"- следующий шаг: {cont['nextStep']}")
    matched_required = [item['label'] for item in cont.get('requiredEvidenceVerdicts', []) if item.get('matched')]
    missing_required = [item['label'] for item in cont.get('requiredEvidenceVerdicts', []) if not item.get('matched')]
    matched_soft = [item['label'] for item in cont.get('softEvidenceVerdicts', []) if item.get('matched')]
    if matched_required:
        lines.append('- уже подтверждено:')
        for item in matched_required:
            lines.append(f"  - {item}")
    if matched_soft:
        lines.append('- косвенно видно:')
        for item in matched_soft:
            lines.append(f"  - {item}")
    if missing_required:
        lines.append('- чего не хватает:')
        for item in missing_required:
            lines.append(f"  - {item}")
    if cont.get('practicalActions'):
        lines.append('- что делать сейчас:')
        for action in cont['practicalActions']:
            lines.append(f"  - {action}")
    if cont.get('recommendedContent'):
        lines.append('- что ещё посмотреть:')
        for item in cont['recommendedContent']:
            title = item.get('title') or item.get('id')
            href = item.get('href')
            lines.append(f"  - {title}{f' ({href})' if href else ''}")
    if cont.get('fallbackQuestions'):
        lines.append('- что уточнить:')
        for q in cont['fallbackQuestions']:
            lines.append(f"  - {q}")
    return '\n'.join(lines)


def build_human_verify(result):
    lines = []
    lines.append(f"Проверка по {result['lessonId']}:")
    if result.get('title'):
        lines.append(f"- {result['title']}")
    if result.get('href'):
        lines.append(f"- ссылка: {result['href']}")
    lines.append(f"- состояние: {result.get('state')}")
    if result.get('matchedRequired'):
        lines.append('- уже подтверждено:')
        for item in result['matchedRequired']:
            lines.append(f"  - {item['label']}")
    if result.get('matchedSoft'):
        lines.append('- косвенно видно:')
        for item in result['matchedSoft']:
            lines.append(f"  - {item['label']}")
    if result.get('missingRequired'):
        lines.append('- пока не найдено локально:')
        for item in result['missingRequired']:
            lines.append(f"  - {item['label']}")
    if result.get('nextStep'):
        lines.append(f"- следующий шаг: {result['nextStep']}")
    if result.get('fallbackQuestions'):
        lines.append('- что уточнить:')
        for item in result['fallbackQuestions']:
            lines.append(f"  - {item}")
    return '\n'.join(lines)


def build_human_next_action(result):
    lines = [result['message']]
    if result.get('title'):
        lines.append(f"- {result['title']}")
    if result.get('lessonId'):
        lines.append(f"- урок: {result['lessonId']}")
    if result.get('href'):
        lines.append(f"- ссылка: {result['href']}")
    if result.get('nextStep'):
        lines.append(f"- следующий шаг: {result['nextStep']}")
    if result.get('actions'):
        lines.append('- что делать сейчас:')
        for action in result['actions']:
            lines.append(f"  - {action}")
    return '\n'.join(lines)


def build_human_sync(sync_result):
    lines = []
    if sync_result.get('applied'):
        lines.append('Автосинхронизация с Human20:')
        for item in sync_result['applied']:
            lines.append(f"- {item['lessonId']}: {', '.join(item['actions'])}")
    noteworthy_skips = [item for item in sync_result.get('skipped', []) if item.get('reason') not in {'already-synced', 'not-verified'}]
    if noteworthy_skips:
        lines.append('Что синхронизация специально не трогала:')
        for item in noteworthy_skips:
            lesson_id = item['lessonId']
            reason = item.get('reason')
            if reason == 'missing-homework-catalog':
                lines.append(f"- {lesson_id}: нет канонического каталога homework task ids")
            elif reason == 'unexpected-live-task-ids':
                extra = ', '.join(item.get('unexpectedLiveTaskIds', []))
                lines.append(f"- {lesson_id}: live task ids расходятся с локальным mapping ({extra})")
            else:
                lines.append(f"- {lesson_id}: {reason}")
    if sync_result.get('errors'):
        lines.append('Что не удалось синхронизировать:')
        for item in sync_result['errors']:
            lines.append(f"- {item['lessonId']} / {item['action']}: {item['error']}")
    return '\n'.join(lines)


def extract_error_text(result):
    if isinstance(result, dict) and isinstance(result.get('text'), str):
        return result['text']
    return None


def build_autopass_experiment(client, workshop, progress, local):
    tools_raw = client.list_tools()
    tools = []
    for item in tools_raw.get('result', {}).get('tools', []):
        annotations = item.get('annotations') or {}
        tools.append({
            'name': item['name'],
            'readOnly': annotations.get('readOnlyHint'),
            'destructive': annotations.get('destructiveHint'),
            'idempotent': annotations.get('idempotentHint'),
        })

    progress_items = progress.get('completedItems', []) if isinstance(progress, dict) else []
    lesson_ids = [lesson['id'] for lesson in workshop.get('lessons', [])]
    homework_progress = safe_call(client, 'get_homework_progress')
    homework_error = extract_error_text(homework_progress)

    reset_supported = any(tool['name'] in {'reset_progress', 'unmark_complete', 'mark_incomplete'} for tool in tools)
    reset_plan = {
        'possible': reset_supported,
        'reason': 'No reset/unmark tool is exposed by Human20 MCP.' if not reset_supported else 'Reset tool detected.',
        'attempted': False,
        'changedItems': [],
    }

    progression = []
    blocked_by_homework = False
    for lesson_id in lesson_ids:
        already_complete = lesson_id in progress_items
        step = {
            'lessonId': lesson_id,
            'alreadyComplete': already_complete,
            'action': 'none' if already_complete else 'mark_complete',
            'status': 'already-complete' if already_complete else 'ready-to-mark',
            'reason': 'Lesson is already marked complete in Human20.' if already_complete else 'Lesson is incomplete and can be marked sequentially if homework does not block it.',
        }
        if not already_complete and homework_error:
            step['status'] = 'blocked-homework-ambiguity'
            step['action'] = 'none'
            step['reason'] = homework_error
            blocked_by_homework = True
        progression.append(step)

    safe_limit = 'Cannot safely automate homework because get_homework_progress is ambiguous and toggle_homework_task is a raw toggle.'
    if not homework_error:
        safe_limit = 'Can inspect homework state, but should still toggle only when current state is unambiguous.'

    return {
        'availableTools': tools,
        'reset': reset_plan,
        'homework': {
            'readResult': homework_progress,
            'safeWrite': False,
            'reason': safe_limit,
        },
        'progression': progression,
        'summary': {
            'allLessonsAlreadyComplete': all(lesson_id in progress_items for lesson_id in lesson_ids),
            'blockedByHomework': blocked_by_homework,
            'canAdvanceSequentiallyNow': any(step['status'] == 'ready-to-mark' for step in progression) and not blocked_by_homework,
        }
    }


def build_test_trainer_flow(workshop, local):
    lessons = local['lessons']
    lesson_map = workshop_lesson_map(workshop)
    simulation = []
    all_previous_gates_open = True

    for index, lesson in enumerate(lessons):
        previous_lesson = lessons[index - 1] if index > 0 else None
        previous_homework_gate_open = previous_lesson is None or previous_lesson['status'] == 'auto-pass'

        if not previous_homework_gate_open:
            all_previous_gates_open = False

        if not all_previous_gates_open:
            simulated_status = 'blocked-by-homework-gate'
            next_step = f"Сначала подтвердить урок {previous_lesson['id']} и считать его homework gate закрытым в тестовой симуляции."
        elif lesson['status'] == 'auto-pass':
            simulated_status = 'already-confirmed'
            next_step = 'Урок уже локально подтверждён, можно переходить дальше в рамках тестовой симуляции.'
        else:
            simulated_status = 'ready-for-simulated-run'
            next_step = lesson['nextStep']

        simulation.append({
            'lessonId': lesson['id'],
            'title': lesson['title'],
            'href': lesson_map.get(lesson['id'], {}).get('href'),
            'localStatus': lesson['status'],
            'simulatedStatus': simulated_status,
            'homeworkGate': {
                'gatedByLessonId': previous_lesson['id'] if previous_lesson else None,
                'open': previous_homework_gate_open,
                'rule': 'Следующий урок в test-only режиме открывается только если предыдущий урок локально имеет status auto-pass.',
            },
            'nextStep': next_step,
            'fallbackQuestions': lesson.get('fallbackQuestions', []),
        })

    current_step = next((item for item in simulation if item['simulatedStatus'] == 'ready-for-simulated-run'), None)
    blocked_step = next((item for item in simulation if item['simulatedStatus'] == 'blocked-by-homework-gate'), None)

    return {
        'mode': 'test-trainer',
        'testOnly': True,
        'safeWrite': False,
        'positioning': 'Test-only fallback trainer/orchestrator mode. Не меняет обычный /human20 flow и не пишет в Human20.',
        'gatingRule': 'Homework gate моделируется только на уровне логики, без toggle/write-back в Human20.',
        'simulation': simulation,
        'currentStep': current_step,
        'blockedStep': blocked_step,
    }


def build_human_test_trainer_output(result):
    lines = []
    lines.append('TEST-ONLY trainer/orchestrator mode')
    lines.append('- write в Human20 отключён')
    lines.append('- уроки идут строго последовательно')
    lines.append('- homework gate учитывается только на уровне логики')
    lines.append('')
    for item in result['simulation']:
        lines.append(f"- {item['lessonId']}: {item['simulatedStatus']} (local: {item['localStatus']})")
        if item['homeworkGate']['gatedByLessonId']:
            gate_state = 'open' if item['homeworkGate']['open'] else 'closed'
            lines.append(f"  - homework gate by {item['homeworkGate']['gatedByLessonId']}: {gate_state}")
        if item['simulatedStatus'] in {'ready-for-simulated-run', 'blocked-by-homework-gate'}:
            lines.append(f"  - next step: {item['nextStep']}")
            if item.get('href'):
                lines.append(f"  - ссылка: {item['href']}")
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='human20-helper MVP flow')
    parser.add_argument('--mode', choices=['summary', 'human', 'whats-new', 'changed-since', 'continue', 'verify', 'next-action', 'autopass-experiment', 'test-trainer'], default='summary')
    parser.add_argument('--since', help='ISO timestamp for changed-since mode')
    parser.add_argument('--lesson', help='Lesson id for continuation mode, e.g. lesson-4')
    args = parser.parse_args()

    local = evaluate(RULES)

    if args.mode == 'test-trainer':
        try:
            workshop = safe_call(Human20McpClient(), 'get_workshop')
        except Human20McpError:
            workshop = {'lessons': []}
        result = build_test_trainer_flow(workshop, local)
        print(build_human_test_trainer_output(result))
        return

    client = Human20McpClient()

    if args.mode == 'whats-new':
        print(json.dumps(build_whats_new(client), ensure_ascii=False, indent=2))
        return
    if args.mode == 'changed-since':
        if not args.since:
            raise SystemExit('--since is required for --mode changed-since')
        print(json.dumps(build_changed_since(client, args.since), ensure_ascii=False, indent=2))
        return

    workshop = safe_call(client, 'get_workshop')
    progress = safe_call(client, 'get_progress')
    onboarding = safe_call(client, 'get_onboarding')
    digest = safe_call(client, 'get_digest')
    homework_progress = safe_call(client, 'get_homework_progress')
    homework_catalogs = fetch_homework_catalogs(client, local)
    sync_lesson_ids = None
    if args.mode == 'continue' and args.lesson:
        sync_lesson_ids = [args.lesson]
    elif args.mode == 'verify':
        verify_lesson = args.lesson or (first_unfinished(local['lessons']) or {}).get('id') or 'lesson-1'
        sync_lesson_ids = [verify_lesson]
    sync_result = run_auto_sync(client, progress, homework_progress, local, homework_catalogs, lesson_ids=sync_lesson_ids)
    progress = sync_result.get('progress', progress)
    homework_progress = sync_result.get('homework', homework_progress)

    if args.mode == 'continue':
        if not args.lesson:
            raise SystemExit('--lesson is required for --mode continue')
        cont = build_continuation(args.lesson, workshop, digest, local)
        text = build_human_continuation(cont)
        sync_text = build_human_sync(sync_result)
        print('\n\n'.join(part for part in [text, sync_text] if part))
        return

    if args.mode == 'verify':
        lesson_id = args.lesson or (first_unfinished(local['lessons']) or {}).get('id') or 'lesson-1'
        result = build_verify(lesson_id, workshop, local)
        text = build_human_verify(result)
        sync_text = build_human_sync(sync_result)
        print('\n\n'.join(part for part in [text, sync_text] if part))
        return

    if args.mode == 'next-action':
        summary = build_summary(workshop, progress, onboarding, digest, local, homework_progress, homework_catalogs)
        result = build_next_action(summary)
        text = build_human_next_action(result)
        sync_text = build_human_sync(sync_result)
        print('\n\n'.join(part for part in [text, sync_text] if part))
        return

    if args.mode == 'autopass-experiment':
        experiment = build_autopass_experiment(client, workshop, progress, local)
        print(json.dumps(experiment, ensure_ascii=False, indent=2))
        return

    summary = build_summary(workshop, progress, onboarding, digest, local, homework_progress, homework_catalogs)
    if args.mode == 'human':
        text = build_human_output(summary)
        sync_text = build_human_sync(sync_result)
        print('\n\n'.join(part for part in [text, sync_text] if part))
        return
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
