#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

import helper_flow
import entrypoint


class Human20HelperFlowTests(unittest.TestCase):
    def test_paths_are_skill_local(self):
        skill_root = Path(__file__).resolve().parents[1]
        self.assertEqual(entrypoint.ROOT, skill_root)
        self.assertEqual(entrypoint.HELPER, skill_root / 'scripts' / 'helper_flow.py')
        self.assertEqual(helper_flow.ROOT, skill_root)
        self.assertEqual(helper_flow.RULES, skill_root / 'data' / 'lesson_rules.json')

    def test_first_unfinished_keeps_default_flow_logic(self):
        lessons = [
            {'id': 'lesson-1', 'status': 'auto-pass'},
            {'id': 'lesson-2', 'status': 'soft-pass'},
            {'id': 'lesson-3', 'status': 'manual'},
        ]
        result = helper_flow.first_unfinished(lessons)
        self.assertEqual(result['id'], 'lesson-2')

    def test_test_trainer_blocks_next_lesson_until_previous_is_auto_pass(self):
        local = {
            'lessons': [
                {'id': 'lesson-1', 'title': 'Lesson 1', 'status': 'auto-pass', 'nextStep': 'done', 'fallbackQuestions': []},
                {'id': 'lesson-2', 'title': 'Lesson 2', 'status': 'manual', 'nextStep': 'finish lesson 2', 'fallbackQuestions': []},
                {'id': 'lesson-3', 'title': 'Lesson 3', 'status': 'manual', 'nextStep': 'finish lesson 3', 'fallbackQuestions': []},
            ]
        }
        workshop = {'lessons': [{'id': 'lesson-2', 'href': 'https://example.com/lesson-2'}]}

        result = helper_flow.build_test_trainer_flow(workshop, local)

        self.assertTrue(result['testOnly'])
        self.assertFalse(result['safeWrite'])
        self.assertEqual(result['currentStep']['lessonId'], 'lesson-2')
        self.assertEqual(result['currentStep']['simulatedStatus'], 'ready-for-simulated-run')
        self.assertEqual(result['blockedStep']['lessonId'], 'lesson-3')
        self.assertEqual(result['blockedStep']['simulatedStatus'], 'blocked-by-homework-gate')

    def test_entrypoint_infers_test_trainer_mode(self):
        mode, lesson, since = entrypoint.infer_mode('включи тестовый режим')
        self.assertEqual((mode, lesson, since), ('test-trainer', None, None))

    def test_human_output_shows_explainable_verdicts(self):
        summary = {
            'localLessons': [
                {
                    'id': 'lesson-1',
                    'status': 'auto-pass',
                    'evidenceSummary': {'requiredTotal': 3, 'requiredMatched': 3, 'softTotal': 1, 'softMatched': 1},
                    'requiredEvidenceVerdicts': [
                        {'label': 'Найден проект portfolio-site', 'matched': True},
                    ],
                    'softEvidenceVerdicts': [],
                },
                {
                    'id': 'lesson-2',
                    'status': 'soft-pass',
                    'evidenceSummary': {'requiredTotal': 2, 'requiredMatched': 1, 'softTotal': 2, 'softMatched': 1},
                    'requiredEvidenceVerdicts': [
                        {'label': 'Найден git remote проекта', 'matched': True},
                        {'label': 'Есть следы деплоя/Vercel', 'matched': False},
                    ],
                    'softEvidenceVerdicts': [
                        {'label': 'Есть публичная ссылка на сайт', 'matched': True},
                    ],
                },
            ],
            'nextLesson': {
                'id': 'lesson-2',
                'title': 'Lesson 2',
                'href': '/content/lesson-2',
                'nextStep': 'Проверить деплой.',
                'practicalActions': ['Открыть сайт.'],
                'fallbackQuestions': [],
            },
        }
        text = helper_flow.build_human_output(summary)
        self.assertIn('частично подтверждено (required 1/2, soft 1/2)', text)
        self.assertIn('уже подтверждено:', text)
        self.assertIn('пока не найдено локально:', text)
        self.assertIn('что делать сейчас:', text)

    def test_build_summary_falls_back_to_homework_when_progress_is_empty(self):
        local = {
            'lessons': [
                {'id': 'lesson-1', 'title': 'Lesson 1', 'status': 'manual', 'nextStep': 'finish lesson 1', 'fallbackQuestions': []},
                {'id': 'lesson-2', 'title': 'Lesson 2', 'status': 'manual', 'nextStep': 'finish lesson 2', 'fallbackQuestions': []},
            ]
        }
        workshop = {'lessons': [{'id': 'lesson-2', 'href': '/content/lesson-2'}]}
        progress = {'completedItems': [], 'activeItem': None, 'completedCount': None}
        homework = {'progress': {'lesson-1': ['l1-1', 'l1-2', 'l1-3', 'l1-4'], 'lesson-2': ['l2-1']}}

        summary = helper_flow.build_summary(workshop, progress, {}, {}, local, homework)

        self.assertTrue(summary['useHomeworkProgress'])
        self.assertEqual(summary['homeworkLessons'][0]['lessonId'], 'lesson-1')
        self.assertTrue(summary['homeworkLessons'][0]['done'])
        lesson2 = next(item for item in summary['homeworkLessons'] if item['lessonId'] == 'lesson-2')
        self.assertFalse(lesson2['done'])
        self.assertEqual(summary['nextLesson']['id'], 'lesson-2')

    def test_human_output_formats_homework_fallback(self):
        summary = {
            'useHomeworkProgress': True,
            'homeworkLessons': [
                {'lessonId': 'lesson-1', 'completedCount': 4, 'expectedCount': 4, 'done': True},
                {'lessonId': 'lesson-2', 'completedCount': 2, 'expectedCount': 2, 'done': True},
            ],
            'nextLesson': {
                'id': None,
                'title': None,
                'href': None,
                'nextStep': 'Все основные уроки локально подтверждены.',
                'practicalActions': [],
            },
            'localLessons': [],
        }

        text = helper_flow.build_human_output(summary)

        self.assertIn('Текущее состояние по урокам (по homework в Human20):', text)
        self.assertIn('- lesson-1: завершено (4/4)', text)
        self.assertIn('Все основные уроки закрыты по homework в Human20.', text)

    def test_build_verify_reports_missing_required(self):
        local = {
            'lessons': [
                {
                    'id': 'lesson-3',
                    'title': 'Lesson 3',
                    'status': 'soft-pass',
                    'requiredEvidenceVerdicts': [
                        {'label': 'MiniMax настроен в конфиге', 'matched': True},
                        {'label': 'Навык webd найден', 'matched': False},
                    ],
                    'softEvidenceVerdicts': [],
                    'nextStep': 'Добить webd.',
                    'fallbackQuestions': [],
                }
            ]
        }
        workshop = {'lessons': [{'id': 'lesson-3', 'href': '/content/lesson-3'}]}
        result = helper_flow.build_verify('lesson-3', workshop, local)
        self.assertEqual(result['state'], 'needs_work')
        self.assertEqual(len(result['missingRequired']), 1)
        self.assertEqual(result['missingRequired'][0]['label'], 'Навык webd найден')

    def test_build_next_action_uses_next_lesson(self):
        summary = {
            'nextLesson': {
                'id': 'lesson-2',
                'title': 'Lesson 2',
                'state': 'needs_work',
                'href': '/content/lesson-2',
                'nextStep': 'Проверить деплой.',
                'practicalActions': ['Открыть сайт.'],
            }
        }
        result = helper_flow.build_next_action(summary)
        self.assertEqual(result['lessonId'], 'lesson-2')
        self.assertEqual(result['state'], 'needs_work')
        self.assertIn('lesson-2', result['message'])

    def test_build_auto_sync_plan_marks_verified_lessons_only(self):
        local = {
            'lessons': [
                {
                    'id': 'lesson-1',
                    'status': 'auto-pass',
                    'requiredEvidenceVerdicts': [{'matched': True}],
                },
                {
                    'id': 'lesson-2',
                    'status': 'soft-pass',
                    'requiredEvidenceVerdicts': [{'matched': True}, {'matched': False}],
                },
            ]
        }
        progress = {'completedItems': []}
        homework = {'progress': {'lesson-1': ['l1-1'], 'lesson-2': []}}
        plan = helper_flow.build_auto_sync_plan(progress, homework, local)
        lesson1 = next(x for x in plan if x['lessonId'] == 'lesson-1')
        lesson2 = next(x for x in plan if x['lessonId'] == 'lesson-2')
        self.assertTrue(lesson1['eligible'])
        self.assertTrue(lesson1['syncSafe'])
        self.assertTrue(lesson1['markComplete'])
        self.assertIn('l1-2', lesson1['missingHomeworkTaskIds'])
        self.assertFalse(lesson2['eligible'])

    def test_build_auto_sync_plan_prefers_live_homework_catalog(self):
        local = {
            'lessons': [
                {
                    'id': 'lesson-1',
                    'status': 'auto-pass',
                    'requiredEvidenceVerdicts': [{'matched': True}],
                }
            ]
        }
        progress = {'completedItems': []}
        homework = {'progress': {'lesson-1': ['legacy-local-value']}}
        catalogs = {
            'lesson-1': {
                'lesson_id': 'lesson-1',
                'tasks': [
                    {'task_id': 'l1-1', 'label': 'Task 1', 'completed': True},
                    {'task_id': 'l1-2', 'label': 'Task 2', 'completed': False},
                ],
            }
        }
        plan = helper_flow.build_auto_sync_plan(progress, homework, local, catalogs)
        lesson1 = next(x for x in plan if x['lessonId'] == 'lesson-1')
        self.assertEqual(lesson1['mappingSource'], 'get_homework_catalog')
        self.assertTrue(lesson1['syncSafe'])
        self.assertEqual(lesson1['expectedHomeworkTaskIds'], ['l1-1', 'l1-2'])
        self.assertEqual(lesson1['missingHomeworkTaskIds'], ['l1-2'])

    def test_build_auto_sync_plan_can_be_scoped_to_single_lesson(self):
        local = {
            'lessons': [
                {
                    'id': 'lesson-1',
                    'status': 'auto-pass',
                    'requiredEvidenceVerdicts': [{'matched': True}],
                },
                {
                    'id': 'lesson-2',
                    'status': 'auto-pass',
                    'requiredEvidenceVerdicts': [{'matched': True}],
                },
            ]
        }
        progress = {'completedItems': []}
        homework = {'progress': {'lesson-1': ['l1-1'], 'lesson-2': ['l2-1']}}

        plan = helper_flow.build_auto_sync_plan(progress, homework, local, lesson_ids=['lesson-1'])

        self.assertEqual([item['lessonId'] for item in plan], ['lesson-1'])
        self.assertIn('l1-2', plan[0]['missingHomeworkTaskIds'])

    def test_build_auto_sync_plan_blocks_on_unexpected_live_task_ids(self):
        local = {
            'lessons': [
                {
                    'id': 'lesson-1',
                    'status': 'auto-pass',
                    'requiredEvidenceVerdicts': [{'matched': True}],
                }
            ]
        }
        progress = {'completedItems': []}
        homework = {'progress': {'lesson-1': ['l1-1', 'l1-x']}}
        plan = helper_flow.build_auto_sync_plan(progress, homework, local)
        lesson1 = next(x for x in plan if x['lessonId'] == 'lesson-1')
        self.assertFalse(lesson1['syncSafe'])
        self.assertEqual(lesson1['skipReason'], 'unexpected-live-task-ids')
        self.assertIn('l1-x', lesson1['unexpectedLiveTaskIds'])

    def test_build_human_sync_formats_applied_actions(self):
        text = helper_flow.build_human_sync({'applied': [{'lessonId': 'lesson-3', 'actions': ['mark_complete', 'toggle_homework_task:l3-1']}], 'errors': []})
        self.assertIn('Автосинхронизация с Human20:', text)
        self.assertIn('lesson-3', text)

    def test_build_human_sync_formats_noteworthy_skip(self):
        text = helper_flow.build_human_sync({'applied': [], 'errors': [], 'skipped': [{'lessonId': 'lesson-1', 'reason': 'unexpected-live-task-ids', 'unexpectedLiveTaskIds': ['l1-x']}]})
        self.assertIn('специально не трогала', text)
        self.assertIn('l1-x', text)


if __name__ == '__main__':
    unittest.main()
