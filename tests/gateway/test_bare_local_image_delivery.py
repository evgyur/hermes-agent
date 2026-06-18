from pathlib import Path

from gateway.platforms.base import BasePlatformAdapter


def test_bare_local_image_paths_are_not_auto_delivered(tmp_path):
    image = tmp_path / "stale_preview.png"
    image.write_bytes(b"fake image bytes")
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF")

    paths, cleaned = BasePlatformAdapter.extract_local_files(
        f"готово {image} {pdf}"
    )
    filtered = BasePlatformAdapter.filter_local_delivery_paths(paths)

    assert str(image) in paths
    assert str(pdf) in paths
    assert str(image) not in filtered
    assert str(pdf) in filtered
    assert str(image) not in cleaned
    assert str(pdf) not in cleaned


def test_explicit_media_image_delivery_still_allowed(tmp_path):
    image = tmp_path / "final.png"
    image.write_bytes(b"fake image bytes")

    media, cleaned = BasePlatformAdapter.extract_media(f"MEDIA:{image}")
    filtered = BasePlatformAdapter.filter_media_delivery_paths(media)

    assert cleaned == ""
    assert filtered == [(str(image), False)]
