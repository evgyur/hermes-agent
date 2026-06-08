"""Telegram reflected outbound-message echo suppression."""

from types import SimpleNamespace

from gateway.platforms.telegram import TelegramAdapter


def _message(chat_id="617744661", text="", message_id=123):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        text=text,
        caption=None,
        message_id=message_id,
    )


def test_recent_outbound_text_echo_matches_markdown_stripped_reply_preview():
    adapter = object.__new__(TelegramAdapter)

    outbound = (
        "Доработал rentals и выкатил в прод. Дублем через `telegram-chip` / ChipCR ничего не отправлял.\n\n"
        "➊ что изменил\n"
        "┈ **убрал фейковую выдачу ChatGPT-кода**\n"
        "┈ `setupSecret` защищает bootstrap-report"
    )
    inbound = (
        "[Replying to: \"Давай наш проект rentals доработай тогда\"]\n\n"
        "Доработал rentals и выкатил в прод. Дублем через telegram-chip / ChipCR ничего не отправлял.\n\n"
        "➊ что изменил\n"
        "┈ убрал фейковую выдачу ChatGPT-кода\n"
        "┈ setupSecret защищает bootstrap-report"
    )

    adapter._remember_recent_outbound_text("617744661", outbound)

    assert adapter._is_recent_outbound_text_echo(_message(text=inbound)) is True


def test_recent_outbound_text_echo_does_not_hide_real_followup():
    adapter = object.__new__(TelegramAdapter)

    outbound = "Короткий ответ с `кодом` и **жирным** текстом."
    inbound = (
        "[Replying to: \"Короткий ответ с кодом и жирным текстом.\"]\n\n"
        "Короткий ответ с кодом и жирным текстом.\n\n"
        "Вот это ты зачем дублируешь от меня как бы сюда в чат!? Чини"
    )

    adapter._remember_recent_outbound_text("617744661", outbound)

    assert adapter._is_recent_outbound_text_echo(_message(text=inbound)) is False
