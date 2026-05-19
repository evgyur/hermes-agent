from gateway.platforms.base import _IMAGE_CAPTION_LIMIT, _caption_text_for_images


def test_short_text_can_be_folded_into_image_caption():
    assert _caption_text_for_images("HYPE: $46.99 USD ⬆️ +3.38% over 24h", 1) == (
        "HYPE: $46.99 USD ⬆️ +3.38% over 24h"
    )


def test_caption_fold_requires_native_image():
    assert _caption_text_for_images("plain text", 0) is None


def test_overlong_text_stays_as_text_message():
    overlong = "x" * (_IMAGE_CAPTION_LIMIT + 1)
    assert _caption_text_for_images(overlong, 1) is None
