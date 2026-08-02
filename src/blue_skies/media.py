from __future__ import annotations

import csv
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .color import FloatImage, srgb_to_linear


@dataclass(frozen=True, slots=True)
class Source:
    uuid: str
    path: Path
    date_created: str
    original_filename: str


IMAGE_EXTENSIONS = {
    ".avif",
    ".dng",
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
}


def load_sources(manifest: Path, source_dir: Path) -> list[Source]:
    with manifest.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    sources: list[Source] = []
    for row in rows:
        uuid = row["asset_uuid"]
        candidates = sorted(
            path
            for path in source_dir.glob(f"{uuid}.*")
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        )
        if not candidates:
            raise FileNotFoundError(f"No exported original found for {uuid} in {source_dir}")
        if len(candidates) > 1:
            candidates.sort(key=lambda path: path.stat().st_size, reverse=True)
        sources.append(
            Source(
                uuid=uuid,
                path=candidates[0].resolve(),
                date_created=row["date_created"],
                original_filename=row["original_filename"],
            )
        )
    if not sources:
        raise ValueError(f"No sources found in {manifest}")
    missing = [source.path for source in sources if not source.path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing {len(missing)} source files; first: {missing[0]}")
    return sources


def decode_square(path: Path, size: int) -> FloatImage:
    """Decode, autorotate, center-crop, and return a square linear-light image."""
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-filter_complex",
        (
            f"[0:v]scale={size}:{size}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={size}:{size},setsar=1,format=rgb48le[out]"
        ),
        "-map",
        "[out]",
        "-frames:v",
        "1",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb48le",
        "pipe:1",
    ]
    completed = subprocess.run(command, check=False, capture_output=True)
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg could not decode {path}: {message}")
    expected_values = size * size * 3
    pixels = np.frombuffer(completed.stdout, dtype="<u2")
    if pixels.size != expected_values:
        raise RuntimeError(
            f"ffmpeg decoded {pixels.size} values from {path}; expected {expected_values}"
        )
    srgb = pixels.reshape(size, size, 3).astype(np.float32) / 65535.0
    return srgb_to_linear(srgb)


def resize_float(image: FloatImage, size: int) -> FloatImage:
    """Resize a floating-point RGB image channel-by-channel without quantization."""
    height, width, channels = image.shape
    if height == size and width == size:
        return image.copy()
    result = np.empty((size, size, channels), dtype=np.float32)
    for channel in range(channels):
        plane = Image.fromarray(image[..., channel], mode="F")
        result[..., channel] = np.asarray(
            plane.resize((size, size), resample=Image.Resampling.LANCZOS),
            dtype=np.float32,
        )
    return result


def down_up(image: FloatImage, reduced_size: int) -> FloatImage:
    """Return a low-pass reconstruction through a reduced floating-point image."""
    original_size = image.shape[0]
    return resize_float(resize_float(image, reduced_size), original_size)


def write_contact_sheet(
    ordered_sources: list[Source], destination: Path, *, tile_size: int = 160
) -> None:
    columns = 10
    rows = (len(ordered_sources) + columns - 1) // columns
    canvas = Image.new("RGB", (columns * tile_size, rows * tile_size), "black")
    for index, source in enumerate(ordered_sources):
        linear = decode_square(source.path, tile_size)
        from .color import linear_to_srgb

        pixels = np.rint(np.clip(linear_to_srgb(linear), 0.0, 1.0) * 255.0).astype(np.uint8)
        tile = Image.fromarray(pixels, mode="RGB")
        x = (index % columns) * tile_size
        y = (index // columns) * tile_size
        canvas.paste(tile, (x, y))
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, optimize=True)
