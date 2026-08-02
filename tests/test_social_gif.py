from pathlib import Path

import pytest

from blue_skies.social_gif import SocialGifSettings, find_largest_fitting_dimension


def test_finds_largest_fitting_dimension(tmp_path: Path) -> None:
    settings = SocialGifSettings(
        max_bytes=1_850,
        min_dimension=100,
        max_dimension=200,
        dimension_step=10,
    )

    def encode(dimension: int, destination: Path) -> int:
        destination.write_bytes(b"GIF")
        return dimension * 10

    result = find_largest_fitting_dimension(encode, tmp_path, settings)
    assert result.dimension == 180
    assert result.size_bytes == 1_800
    assert result.path.is_file()


def test_raises_when_smallest_dimension_exceeds_limit(tmp_path: Path) -> None:
    settings = SocialGifSettings(
        max_bytes=999,
        min_dimension=100,
        max_dimension=200,
        dimension_step=10,
    )

    def encode(dimension: int, destination: Path) -> int:
        return dimension * 10

    with pytest.raises(RuntimeError, match="Even 100x100 exceeds 999 bytes"):
        find_largest_fitting_dimension(encode, tmp_path, settings)
