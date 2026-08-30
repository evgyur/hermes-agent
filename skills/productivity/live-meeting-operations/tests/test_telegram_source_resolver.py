import importlib.util
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "telegram_source_resolver.py"
SPEC = importlib.util.spec_from_file_location("telegram_source_resolver", MODULE_PATH)
assert SPEC and SPEC.loader
resolver = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = resolver
SPEC.loader.exec_module(resolver)


class TelegramSourceResolverTests(unittest.TestCase):
    def test_extracts_plain_entities_text_urls_and_inline_buttons(self):
        text = "😀 Сайт и эфир https://plain.example/path"
        message = {
            "id": 826936,
            "text": text,
            "entities": [
                {"type": "MessageEntityTextUrl", "offset": 3, "length": 4, "text": "Сайт", "url": "https://entity.example/join?token=secret"},
                {"type": "MessageEntityUrl", "offset": 15, "length": 26, "text": "https://plain.example/path"},
            ],
            "reply_markup": {
                "rows": [
                    {"buttons": [{"type": "KeyboardButtonUrl", "text": "Подключиться к эфиру 🔥", "url": "https://button.example/r/abc?auth=secret"}]}
                ]
            },
        }
        candidates = resolver.extract_candidates(message)
        pairs = {(item.source_type, item.url) for item in candidates}
        self.assertIn(("entity_text_url", "https://entity.example/join?token=secret"), pairs)
        self.assertIn(("entity_url", "https://plain.example/path"), pairs)
        self.assertIn(("inline_button", "https://button.example/r/abc?auth=secret"), pairs)
        selected = resolver.select_candidate(candidates)
        self.assertEqual(selected.source_type, "inline_button")
        self.assertIn("Подключиться", selected.label)

    def test_extracts_telegram_chip_inline_buttons_shape(self):
        message = {
            "text": "Подключиться ниже",
            "inline_buttons": [
                {
                    "row": 0,
                    "buttons": [
                        {"type": "url", "text": "Подключиться", "url": "https://room.example/live?token=secret"}
                    ],
                }
            ],
        }
        candidates = resolver.extract_candidates(message)
        self.assertIn(
            ("inline_button", "https://room.example/live?token=secret"),
            {(item.source_type, item.url) for item in candidates},
        )

    def test_utf16_offset_fallback_handles_emoji(self):
        text = "😀 ссылка"
        message = {
            "text": text,
            "entities": [
                {"type": "MessageEntityTextUrl", "offset": 3, "length": 6, "url": "https://example.com/live"}
            ],
        }
        candidate = resolver.extract_candidates(message)[0]
        self.assertEqual(candidate.label, "ссылка")

    def test_safe_receipt_drops_queries_and_hashes_private_urls(self):
        message = {"id": 42, "chat_id": 7, "text": "join"}
        candidate = resolver.Candidate("https://short.example/r/x?token=secret", "inline_button", "join")
        hops = [resolver.RedirectHop(302, "short.example", "https://short.example/r/x")]
        safe, private = resolver.build_receipts(
            message,
            [candidate],
            candidate,
            "https://room.example/live/abc?session=private#frag",
            hops,
        )
        serialized = json.dumps(safe)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("private", serialized)
        self.assertEqual(safe["selection"]["final_url_redacted"], "https://room.example/live/abc")
        self.assertEqual(private["private"]["final_url"], "https://room.example/live/abc?session=private#frag")

    @mock.patch.object(socket, "getaddrinfo")
    def test_blocks_private_resolution_targets(self, getaddrinfo):
        getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
        with self.assertRaises(resolver.SourceResolutionError):
            resolver.validate_public_http_url("http://internal.example/live")

    def test_private_json_is_mode_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipt.json"
            resolver._atomic_private_json(path, {"ok": True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
