from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import entrypoint  # type: ignore  # noqa: E402
import human20_mcp_client  # type: ignore  # noqa: E402


class StubClient:
    def list_tools(self):
        return {
            "result": {
                "tools": [
                    {"name": "get_progress"},
                    {"name": "get_onboarding"},
                    {"name": "get_pulse"},
                    {"name": "get_workshop_chat_json"},
                    {"name": "get_homework_catalog"},
                    {"name": "get_human20_skills_catalog"},
                    {"name": "get_human20_skill"},
                    {"name": "recommend_human20_skills"},
                    {"name": "preview_user_message"},
                    {"name": "send_user_message"},
                ]
            }
        }

    def structured_tool(self, name, arguments=None):
        if name == "get_progress":
            return {"activeItem": "lesson-1", "completedItems": ["lesson-onboarding"]}
        if name == "get_onboarding":
            return {"summary": "start", "status": "resume", "nextMove": "continue"}
        if name == "get_pulse":
            return {
                "title": "Пульс",
                "updatedAt": "2026-04-24",
                "threads": [{"title": "A", "summary": "B"}],
            }
        if name == "get_whats_new":
            return {"summary": "new"}
        if name == "get_workshop_chat_json":
            return {
                "messageCount": 1,
                "messages": [{"message_id": 1, "text": "OpenClaw works", "from": "Example User"}],
            }
        if name == "get_content_detail":
            return {"item": {"title": "Lesson", "href": "/content/lesson-1"}, "attachments": []}
        if name == "get_transcript":
            return [{"text": "hello"}]
        if name == "get_homework_progress":
            return {"progress": {}}
        if name == "get_homework_catalog":
            return {
                "lesson_id": arguments.get("lesson_id"),
                "lesson_title": "Lesson",
                "lesson_number": 1,
                "tasks": [{"task_id": "l1-1", "label": "Task", "completed": False}],
            }
        if name == "get_human20_skills_catalog":
            return {
                "items": [
                    {
                        "slug": "telegram-chip",
                        "title": "Telegram publishing",
                        "summary": "Помогает агенту работать с Telegram: читать чат, готовить дайджесты и искать сигналы.",
                        "tags": ["Telegram", "Автоматизация"],
                        "useCases": ["вести Telegram канал", "делать дайджест"],
                        "docsUrl": "/content/skill-telegram-chip",
                        "zipUrl": "https://example.com/telegram-chip.zip",
                    },
                    {
                        "slug": "workshop-create-skill-practice",
                        "title": "Create Skill Practice",
                        "summary": "Упаковка повторяемой задачи в skill.",
                        "tags": ["Скилы"],
                        "useCases": ["создать skill"],
                        "docsUrl": "/content/skill-workshop-create-skill-practice",
                    },
                ]
            }
        if name == "recommend_human20_skills":
            task = (arguments or {}).get("task", "")
            if "telegram" in task.lower() or "телеграм" in task.lower():
                return {
                    "matches": [
                        {
                            "skill": {
                                "slug": "telegram-chip",
                                "title": "Telegram publishing",
                                "summary": "Помогает агенту работать с Telegram: читать чат, готовить дайджесты и искать сигналы.",
                                "tags": ["Telegram", "Автоматизация"],
                                "useCases": ["вести Telegram канал", "делать дайджест"],
                                "docsUrl": "/content/skill-telegram-chip",
                                "zipUrl": "https://example.com/telegram-chip.zip",
                            },
                            "whyRecommended": "Совпадает с задачей про Telegram.",
                            "score": 42,
                        }
                    ]
                }
            return {"matches": []}
        raise AssertionError(name)


class NoisyRecommendStubClient(StubClient):
    def structured_tool(self, name, arguments=None):
        if name == "recommend_human20_skills":
            return {
                "matches": [
                    {
                        "skill": {
                            "slug": "workshop-bird-research",
                            "title": "Bird Research",
                            "summary": "Поиск сигналов в X/Twitter.",
                            "tags": ["Исследование"],
                            "useCases": ["искать сигналы"],
                        },
                        "whyRecommended": "Сработало по общему слову skill.",
                        "score": 120,
                    }
                ]
            }
        return super().structured_tool(name, arguments)


class Human20HelperEntrypointTest(unittest.TestCase):
    def test_status_reports_missing_expected_tools(self) -> None:
        result = entrypoint.status(StubClient())
        self.assertTrue(result["ok"])
        self.assertIn("get_progress", result["has"])
        self.assertIn("get_content_detail", result["missing"])

    def test_chat_search_returns_matches(self) -> None:
        result = entrypoint.chat_search(StubClient(), "openclaw")
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["matches"][0]["from"], "Example User")

    def test_skill_search_finds_catalog_matches(self) -> None:
        result = entrypoint.skill_search(StubClient(), "дайджест telegram")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["matches"][0]["slug"], "telegram-chip")
        self.assertEqual(result["source"], "get_human20_skills_catalog")

    def test_recommend_skills_uses_mcp_recommendation(self) -> None:
        result = entrypoint.recommend_skills(StubClient(), "какой скил подойдёт для telegram канала")
        self.assertFalse(result["fallbackUsed"])
        self.assertEqual(result["matches"][0]["slug"], "telegram-chip")
        self.assertGreater(result["matches"][0]["score"], 42)

    def test_recommend_skills_uses_catalog_when_backend_has_no_match(self) -> None:
        result = entrypoint.recommend_skills(StubClient(), "создать skill")
        self.assertFalse(result["fallbackUsed"])
        self.assertEqual(result["matches"][0]["slug"], "workshop-create-skill-practice")

    def test_recommend_skills_reranks_with_catalog_exact_match(self) -> None:
        result = entrypoint.recommend_skills(NoisyRecommendStubClient(), "какой скил подойдёт для telegram канала")
        self.assertEqual(result["matches"][0]["slug"], "telegram-chip")
        self.assertGreater(result["matches"][0]["score"], result["matches"][1]["score"])

    def test_human_skill_recommendation_contains_links(self) -> None:
        result = entrypoint.recommend_skills(StubClient(), "какой скил подойдёт для telegram канала")
        text = entrypoint.build_human_skill_recommendation(result)
        self.assertIn("Telegram publishing", text)
        self.assertIn("https://human20.app/content/skill-telegram-chip", text)
        self.assertIn("https://example.com/telegram-chip.zip", text)

    def test_lesson_context_uses_detail_transcript_and_homework(self) -> None:
        result = entrypoint.lesson_context(StubClient(), "lesson-1", None)
        self.assertEqual(result["title"], "Lesson")
        self.assertEqual(result["transcriptChunks"], 1)
        self.assertIn("get_homework_progress", result["sources"])
        self.assertIn("get_homework_catalog", result["sources"])
        self.assertEqual(result["homeworkCatalog"]["tasks"][0]["task_id"], "l1-1")

    def test_entrypoint_infers_verify_mode(self) -> None:
        mode, lesson, since = entrypoint.infer_mode('проверь, что я сделал по уроку 3')
        self.assertEqual((mode, lesson, since), ('verify', 'lesson-3', None))

    def test_entrypoint_infers_next_action_mode(self) -> None:
        mode, lesson, since = entrypoint.infer_mode('веди дальше')
        self.assertEqual((mode, lesson, since), ('next-action', None, None))

    def test_entrypoint_infers_skill_recommend_mode(self) -> None:
        mode, lesson, since = entrypoint.infer_mode('какой скил мне подойдёт для телеграм канала')
        self.assertEqual((mode, lesson, since), ('skill-recommend', None, None))

    def test_client_accepts_token_with_bearer_prefix(self) -> None:
        client = human20_mcp_client.Human20McpClient(
            base_url="https://human20.app/mcp",
            bearer_token="Bearer actual-token",
        )

        self.assertEqual(client.bearer_token, "actual-token")
        self.assertEqual(client._headers(include_session=False)["Authorization"], "Bearer actual-token")


if __name__ == "__main__":
    unittest.main()
