from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SocialGifSettings:
    max_bytes: int = 5_000_000
    fps: int = 15
    colors: int = 128
    min_dimension: int = 96
    max_dimension: int = 480
    dimension_step: int = 2
    bayer_scale: int = 5


@dataclass(frozen=True, slots=True)
class GifCandidate:
    dimension: int
    size_bytes: int
    path: Path


@dataclass(frozen=True, slots=True)
class SocialGifResult:
    destination: Path
    dimension: int
    size_bytes: int
    fps: int
    colors: int


CandidateEncoder = Callable[[int, Path], int]


def _dimensions(settings: SocialGifSettings) -> tuple[int, ...]:
    if settings.max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if settings.fps <= 0:
        raise ValueError("fps must be positive")
    if not 2 <= settings.colors <= 256:
        raise ValueError("colors must be between 2 and 256")
    if settings.dimension_step <= 0:
        raise ValueError("dimension_step must be positive")
    if settings.min_dimension <= 0 or settings.min_dimension > settings.max_dimension:
        raise ValueError("dimensions must be positive and min_dimension <= max_dimension")
    return tuple(
        range(
            settings.min_dimension,
            settings.max_dimension + 1,
            settings.dimension_step,
        )
    )


def find_largest_fitting_dimension(
    encode: CandidateEncoder,
    scratch_dir: Path,
    settings: SocialGifSettings,
) -> GifCandidate:
    """Check dimensions in descending order and return the first GIF that fits."""
    dimensions = _dimensions(settings)

    # GIF size is not monotonic: an oversized result cannot rule out larger sizes.
    for dimension in reversed(dimensions):
        path = scratch_dir / f"candidate-{dimension}.gif"
        size_bytes = encode(dimension, path)
        print(f"{dimension}x{dimension}: {size_bytes:,} bytes")
        if size_bytes <= settings.max_bytes:
            return GifCandidate(dimension=dimension, size_bytes=size_bytes, path=path)
        path.unlink(missing_ok=True)

    raise RuntimeError(
        f"No dimension from {dimensions[0]} to {dimensions[-1]} "
        f"fits within {settings.max_bytes:,} bytes"
    )


def _encode_candidate(
    source: Path,
    destination: Path,
    dimension: int,
    settings: SocialGifSettings,
) -> int:
    frame_filter = f"fps={settings.fps},scale={dimension}:{dimension}:flags=lanczos"
    input_command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(source)]
    # Finish the global palette before decoding again, so frames never queue
    # behind palette generation for the full duration of the source loop.
    with tempfile.TemporaryDirectory(prefix="palette-", dir=destination.parent) as temporary:
        palette = Path(temporary) / "palette.png"
        subprocess.run(
            [
                *input_command,
                "-vf",
                f"{frame_filter},palettegen=max_colors={settings.colors}:stats_mode=full",
                "-frames:v",
                "1",
                "-update",
                "1",
                "-y",
                str(palette),
            ],
            check=True,
        )
        subprocess.run(
            [
                *input_command,
                "-i",
                str(palette),
                "-filter_complex",
                f"[0:v]{frame_filter}[frames];"
                f"[frames][1:v]paletteuse=dither=bayer:bayer_scale={settings.bayer_scale}:"
                "diff_mode=rectangle",
                "-loop",
                "0",
                "-y",
                str(destination),
            ],
            check=True,
        )
    return destination.stat().st_size


def make_social_gif(
    source: Path,
    destination: Path,
    settings: SocialGifSettings,
) -> SocialGifResult:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Source video not found: {source}")
    if source == destination:
        raise ValueError("Destination must not resolve to the source video")
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="blue-skies-farcaster-") as temporary:
        scratch_dir = Path(temporary)

        def encode(dimension: int, candidate_path: Path) -> int:
            return _encode_candidate(source, candidate_path, dimension, settings)

        winner = find_largest_fitting_dimension(encode, scratch_dir, settings)
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_output:
            temporary_destination = Path(temporary_output.name)
        try:
            shutil.copyfile(winner.path, temporary_destination)
            temporary_destination.replace(destination)
        finally:
            temporary_destination.unlink(missing_ok=True)

    return SocialGifResult(
        destination=destination,
        dimension=winner.dimension,
        size_bytes=winner.size_bytes,
        fps=settings.fps,
        colors=settings.colors,
    )
