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
    """Binary-search the largest dimension whose encoded GIF fits the byte ceiling."""
    dimensions = _dimensions(settings)
    low = 0
    high = len(dimensions) - 1
    best: GifCandidate | None = None

    while low <= high:
        middle = (low + high) // 2
        dimension = dimensions[middle]
        path = scratch_dir / f"candidate-{dimension}.gif"
        size_bytes = encode(dimension, path)
        candidate = GifCandidate(dimension=dimension, size_bytes=size_bytes, path=path)
        print(f"{dimension}x{dimension}: {size_bytes:,} bytes")
        if size_bytes <= settings.max_bytes:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1

    if best is None:
        raise RuntimeError(
            f"Even {dimensions[0]}x{dimensions[0]} exceeds {settings.max_bytes:,} bytes"
        )
    return best


def _filter_graph(dimension: int, settings: SocialGifSettings) -> str:
    return (
        f"fps={settings.fps},"
        f"scale={dimension}:{dimension}:flags=lanczos,"
        "split[a][b];"
        f"[a]palettegen=max_colors={settings.colors}:stats_mode=full[p];"
        f"[b][p]paletteuse=dither=bayer:bayer_scale={settings.bayer_scale}:"
        "diff_mode=rectangle"
    )


def _encode_candidate(
    source: Path,
    destination: Path,
    dimension: int,
    settings: SocialGifSettings,
) -> int:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vf",
            _filter_graph(dimension, settings),
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
