import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import GifImagePlugin, Image

from blue_skies.social_gif import (
    SocialGifSettings,
    find_largest_fitting_dimension,
    make_social_gif,
)


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


@pytest.mark.parametrize(
    ("sizes", "expected_dimension"),
    [
        ({166: 29_686, 168: 30_810, 170: 29_947}, 170),
        ({166: 30_001, 168: 30_001, 170: 29_947}, 170),
        ({166: 30_001, 168: 29_947, 170: 30_001}, 168),
    ],
)
def test_finds_largest_fit_with_nonmonotonic_sizes(
    tmp_path: Path, sizes: dict[int, int], expected_dimension: int
) -> None:
    settings = SocialGifSettings(max_bytes=29_947, min_dimension=166, max_dimension=170)

    def encode(dimension: int, destination: Path) -> int:
        destination.write_bytes(bytes(sizes[dimension]))
        return destination.stat().st_size

    result = find_largest_fitting_dimension(encode, tmp_path, settings)
    assert result.dimension == expected_dimension
    assert result.size_bytes == 29_947
    assert result.path.read_bytes() == bytes(29_947)
    assert list(tmp_path.iterdir()) == [result.path]


def test_raises_when_no_dimension_fits(tmp_path: Path) -> None:
    settings = SocialGifSettings(
        max_bytes=999,
        min_dimension=100,
        max_dimension=200,
        dimension_step=10,
    )

    def encode(dimension: int, destination: Path) -> int:
        destination.write_bytes(b"GIF")
        return dimension * 10

    with pytest.raises(RuntimeError, match="No dimension from 100 to 200 fits within 999 bytes"):
        find_largest_fitting_dimension(encode, tmp_path, settings)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("alias", ["same", "relative", "source_symlink", "destination_symlink"])
def test_rejects_destination_resolving_to_source_before_encoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias: str
) -> None:
    source = tmp_path / "source.mp4"
    original_bytes = b"source video must be preserved"
    source.write_bytes(original_bytes)
    destination = source
    if alias == "relative":
        monkeypatch.chdir(tmp_path)
        destination = Path("source.mp4")
    elif alias == "source_symlink":
        source = tmp_path / "source-link.mp4"
        source.symlink_to(destination)
    elif alias == "destination_symlink":
        destination = tmp_path / "destination.gif"
        destination.symlink_to(source)

    encoded_dimensions: list[int] = []

    def encode(source: Path, candidate: Path, dimension: int, settings: SocialGifSettings) -> int:
        encoded_dimensions.append(dimension)
        return candidate.write_bytes(b"GIF")

    monkeypatch.setattr("blue_skies.social_gif._encode_candidate", encode)
    settings = SocialGifSettings(min_dimension=96, max_dimension=96)
    with pytest.raises(ValueError, match="Destination must not resolve to the source video"):
        make_social_gif(source, destination, settings)

    assert encoded_dimensions == []
    assert source.read_bytes() == original_bytes
    assert destination.read_bytes() == original_bytes


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is required")
def test_two_pass_encoding_preserves_full_loop_and_cleans_palette(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.mp4"
    reference = tmp_path / "reference.gif"
    destination = tmp_path / "result.gif"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=192x192:rate=30:duration=1.2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    original_bytes = source.read_bytes()
    # Preserve the established palette, dithering, frame timing, and loop bytes.
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vf",
            "fps=15,scale=96:96:flags=lanczos,split[a][b];"
            "[a]palettegen=max_colors=128:stats_mode=full[p];"
            "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
            "-loop",
            "0",
            str(reference),
        ],
        check=True,
    )

    commands: list[list[str]] = []
    run = subprocess.run

    def record_run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        return run(command, check=check)

    monkeypatch.setattr("blue_skies.social_gif.subprocess.run", record_run)
    settings = SocialGifSettings(min_dimension=96, max_dimension=96)
    result = make_social_gif(source, destination, settings)

    assert result.destination == destination
    assert result.dimension == 96
    assert result.size_bytes == reference.stat().st_size
    assert destination.read_bytes() == reference.read_bytes()
    assert source.read_bytes() == original_bytes
    with Image.open(destination) as gif:
        assert isinstance(gif, GifImagePlugin.GifImageFile)
        assert gif.size == (96, 96)
        assert gif.n_frames == 18
        assert gif.info["loop"] == 0
        duration_ms = 0
        for frame in range(gif.n_frames):
            gif.seek(frame)
            duration_ms += gif.info["duration"]
        assert duration_ms == 1_200

    # Generate the global palette before starting the pass that applies it.
    assert len(commands) == 2
    palette_path = Path(commands[0][-1])
    assert "palettegen=" in commands[0][commands[0].index("-vf") + 1]
    assert str(palette_path) in commands[1]
    assert all(str(source) in command for command in commands)
    assert not palette_path.exists()


@pytest.mark.parametrize("failed_pass", [1, 2])
def test_encoding_failure_preserves_destination_and_removes_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_pass: int
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"original video")
    destination = tmp_path / "result.gif"
    destination.write_bytes(b"previous GIF")
    outputs: list[Path] = []

    def fail_run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[bytes]:
        output = Path(command[-1])
        outputs.append(output)
        output.write_bytes(b"partial encoding")
        if len(outputs) == failed_pass:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("blue_skies.social_gif.subprocess.run", fail_run)
    settings = SocialGifSettings(min_dimension=96, max_dimension=96)
    with pytest.raises(subprocess.CalledProcessError):
        make_social_gif(source, destination, settings)

    assert len(outputs) == failed_pass
    assert all(not output.parent.exists() for output in outputs)
    assert source.read_bytes() == b"original video"
    assert destination.read_bytes() == b"previous GIF"
