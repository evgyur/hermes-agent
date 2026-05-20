#!/usr/bin/env python3
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / 'scripts' / 'generate_present.py'

INPUT = '''## Что это | demo
Короткий текст.

@flow Вход::Пользователь приходит::🔗
@flow Обработка::Рендерим красиво::✨
@flow Выход::Получаем HTML::📄

## Сравнение
@beforeafter Было::Обычный текст без структуры::Стало::Визуальный документ с блоками
'''


def run(mode: str, out_path: Path):
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False, suffix='.md') as f:
        f.write(INPUT)
        input_path = Path(f.name)
    try:
        subprocess.run(
            [
                'python3', str(GEN),
                '--mode', mode,
                '--title', 'Regression Test Present',
                '--subtitle', 'Mode split check',
                '--input', str(input_path),
                '--output', str(out_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        try:
            input_path.unlink()
        except FileNotFoundError:
            pass


def assert_contains(text: str, needle: str, label: str):
    if needle not in text:
        raise AssertionError(f'missing {label}: {needle}')


def assert_not_contains(text: str, needle: str, label: str):
    if needle in text:
        raise AssertionError(f'unexpected {label}: {needle}')


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        general_out = tmp / 'general.html'
        slides_out = tmp / 'slides.html'

        run('general', general_out)
        run('slides', slides_out)

        general_html = general_out.read_text(encoding='utf-8')
        slides_html = slides_out.read_text(encoding='utf-8')

        assert_contains(general_html, 'class="container"', 'general container layout')
        assert_contains(general_html, 'class="aurora"', 'general aurora background')
        assert_not_contains(general_html, 'class="deck"', 'slides deck markup in general')
        assert_not_contains(general_html, 'class="deck-ui"', 'slides controls in general')

        assert_contains(slides_html, 'class="deck"', 'slides deck markup')
        assert_contains(slides_html, 'class="deck-ui"', 'slides controls markup')
        assert_contains(slides_html, 'overflow: hidden;', 'fullscreen body lock in slides')

    print('OK: present general/slides split is preserved')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'FAIL: {e}', file=sys.stderr)
        sys.exit(1)
