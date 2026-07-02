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


def test_recent_outbound_text_echo_matches_markdown_stripped_reply_preview(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
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


def test_recent_outbound_text_echo_does_not_hide_real_followup(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = object.__new__(TelegramAdapter)

    outbound = "Короткий ответ с `кодом` и **жирным** текстом."
    inbound = (
        "[Replying to: \"Короткий ответ с кодом и жирным текстом.\"]\n\n"
        "Короткий ответ с кодом и жирным текстом.\n\n"
        "Вот это ты зачем дублируешь от меня как бы сюда в чат!? Чини"
    )

    adapter._remember_recent_outbound_text("617744661", outbound)

    assert adapter._is_recent_outbound_text_echo(_message(text=inbound)) is False


def test_outbound_text_echo_survives_adapter_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    first = object.__new__(TelegramAdapter)
    first._remember_recent_outbound_text("617744661", "Проверил. По серверу RF третий урок HD отдаётся.")

    restarted = object.__new__(TelegramAdapter)
    inbound = "[Replying to: \"Третий урок hd rf не играет\"]\n\nПроверил. По серверу RF третий урок HD отдаётся."

    assert restarted._is_recent_outbound_text_echo(_message(text=inbound)) is True


def test_outbound_text_echo_matches_across_chat_ids(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = object.__new__(TelegramAdapter)
    outbound = (
        "Сделал: оставил два варианта, но master manifest теперь честно описывает поток.\n\n"
        "➊ что изменил\n"
        "┈ june-lesson-3/stream.m3u8 в RF Timeweb S3"
    )

    adapter._remember_recent_outbound_text("8533179145", outbound)

    # Some relay/userbot paths can reflect the bot-authored text into Chip's
    # primary DM even though the original send was recorded under another chat.
    assert adapter._is_recent_outbound_text_echo(
        _message(chat_id="617744661", text=outbound)
    ) is True


def test_cross_chat_echo_strips_prefix_and_preserves_real_followup(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = object.__new__(TelegramAdapter)
    outbound = "Сделал: оставил два варианта, но master manifest теперь честно описывает поток."
    inbound = outbound + "\n\nКакого чёрта это пришло от моего имени?"

    adapter._remember_recent_outbound_text("8533179145", outbound)

    msg = _message(chat_id="617744661", text=inbound)
    assert adapter._is_recent_outbound_text_echo(msg) is False
    assert adapter._strip_recent_outbound_text_echo_prefix(msg) == "Какого чёрта это пришло от моего имени?"


def test_cross_chat_long_report_prefix_strips_user_complaint(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = object.__new__(TelegramAdapter)
    outbound = (
        "Готово, продолжил до прода.\n\n"
        "➊ что изменил\n"
        "┈ в плеере качество теперь честно подписано: `HQ 2K 60fps` и `SD 480p 60fps`\n"
        "┈ fallback-текст тоже честный: если HQ падает, переключает на `SD 480p 60fps`\n"
        "Остаток: локальный beta-checkout в `/home/hermes/workspace/human20/human20-app` всё ещё грязный."
    )
    inbound = (
        "Готово, продолжил до прода.\n\n"
        "➊ что изменил\n"
        "┈ в плеере качество теперь честно подписано: HQ 2K 60fps и SD 480p 60fps\n"
        "┈ fallback-текст тоже честный: если HQ падает, переключает на SD 480p 60fps\n"
        "Остаток: локальный beta-checkout в /home/hermes/workspace/human20/human20-app всё ещё грязный.\n"
        "Ты снова пишешь\n От моего имени. Ты ничего не исправил. Ты меня обманул"
    )

    adapter._remember_recent_outbound_text("8533179145", outbound)
    msg = _message(chat_id="617744661", text=inbound)

    assert adapter._is_recent_outbound_text_echo(msg) is False
    assert adapter._strip_recent_outbound_text_echo_prefix(msg) == (
        "Ты снова пишешь От моего имени. Ты ничего не исправил. Ты меня обманул"
    )


def test_recent_outbound_reply_quote_marks_content_owned(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = object.__new__(TelegramAdapter)
    outbound = (
        "Коротко: **я продолжил работу после твоего “Продолжаем” и довёл её до production**.\n\n"
        "Что именно:\n"
        "┈ обновил подписи качества в плеере: `HQ 2K 60fps` / `SD 480p 60fps`  \n"
        "┈ обновил генератор HLS master manifest, чтобы он писал честный bitrate/resolution/frame-rate/codecs  \n"
        "Главное: **2K качество не убрали**. Просто теперь оно честно называется `HQ 2K 60fps`."
    )
    telegram_reply_preview = (
        "Коротко: я продолжил работу после твоего “Продолжаем” и довёл её до production.\n\n"
        "Что именно:\n"
        "┈ обновил подписи качества в плеере: HQ 2K 60fps / SD 480p 60fps  \n"
        "┈ обновил генератор HLS master manifest, чтобы он писал честный bitrate/resolution/frame-rate/codecs"
    )

    adapter._remember_recent_outbound_text("8533179145", outbound)

    assert adapter._is_recent_outbound_text_quote("617744661", telegram_reply_preview) is True



def test_should_process_message_drops_self_bot_message():
    adapter = object.__new__(TelegramAdapter)
    adapter._bot = SimpleNamespace(id=42)
    msg = SimpleNamespace(
        from_user=SimpleNamespace(id=42, is_bot=True),
        message_thread_id=None,
        chat=SimpleNamespace(id="617744661"),
    )

    assert adapter._should_process_message(msg) is False
