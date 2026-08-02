from __future__ import annotations

import csv
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from .color import (
    FloatImage,
    linear_rgb_to_oklab,
    linear_to_bt709,
    oklab_to_linear_rgb,
    smootherstep,
)
from .media import Source, decode_square, down_up
from .ordering import Tour


@dataclass(frozen=True, slots=True)
class RenderSettings:
    size: int = 1080
    fps: int = 60
    min_duration: float = 0.45
    max_duration: float = 1.05
    preview_pairs: int | None = None


@dataclass(frozen=True, slots=True)
class PreparedFrame:
    base_lab: FloatImage
    mid: FloatImage
    fine: FloatImage


class ByteWriter(Protocol):
    def write(self, data: bytes, /) -> object: ...


def prepare_frame(source: Source, size: int) -> PreparedFrame:
    image = decode_square(source.path, size)
    quarter = down_up(image, max(8, size // 4))
    base = down_up(image, max(4, size // 16))
    return PreparedFrame(
        base_lab=linear_rgb_to_oklab(base),
        mid=quarter - base,
        fine=image - quarter,
    )


def spline_weights(position: float) -> tuple[float, float, float, float]:
    """Periodic uniform cubic B-spline weights for four adjacent control frames."""
    position = min(1.0, max(0.0, position))
    inverse = 1.0 - position
    return (
        inverse**3 / 6.0,
        (3.0 * position**3 - 6.0 * position**2 + 4.0) / 6.0,
        (-3.0 * position**3 + 3.0 * position**2 + 3.0 * position + 1.0) / 6.0,
        position**3 / 6.0,
    )


def _sharpen_weights(
    weights: tuple[float, float, float, float], power: int
) -> tuple[float, float, float, float]:
    powered = tuple(weight**power for weight in weights)
    total = sum(powered)
    return (
        powered[0] / total,
        powered[1] / total,
        powered[2] / total,
        powered[3] / total,
    )


def interpolate(
    controls: tuple[PreparedFrame, PreparedFrame, PreparedFrame, PreparedFrame],
    position: float,
) -> FloatImage:
    """Blend a continuously moving four-frame segment without stopping at a source."""
    base_weights = spline_weights(position)
    mid_weights = _sharpen_weights(base_weights, 2)
    fine_weights = _sharpen_weights(base_weights, 3)

    base_lab = (
        controls[0].base_lab * base_weights[0]
        + controls[1].base_lab * base_weights[1]
        + controls[2].base_lab * base_weights[2]
        + controls[3].base_lab * base_weights[3]
    )
    base = oklab_to_linear_rgb(base_lab)
    image = (
        base
        + controls[0].mid * mid_weights[0]
        + controls[1].mid * mid_weights[1]
        + controls[2].mid * mid_weights[2]
        + controls[3].mid * mid_weights[3]
        + controls[0].fine * fine_weights[0]
        + controls[1].fine * fine_weights[1]
        + controls[2].fine * fine_weights[2]
        + controls[3].fine * fine_weights[3]
    )
    return np.clip(image, 0.0, 1.0)


def duration_frames(distances: tuple[float, ...], settings: RenderSettings) -> tuple[int, ...]:
    values = np.asarray(distances, dtype=np.float64)
    low = float(np.quantile(values, 0.10))
    high = float(np.quantile(values, 0.90))
    if math.isclose(low, high):
        normalized = np.full_like(values, 0.5)
    else:
        normalized = np.clip((values - low) / (high - low), 0.0, 1.0)
    eased = np.asarray(smootherstep(normalized), dtype=np.float64)
    durations = settings.min_duration + eased * (settings.max_duration - settings.min_duration)
    return tuple(max(2, int(round(float(duration) * settings.fps))) for duration in durations)


def _blue_noise(size: int = 64, seed: int = 0xB1E5) -> FloatImage:
    rng = np.random.default_rng(seed)
    white = rng.normal(size=(size, size))
    frequencies_y = np.fft.fftfreq(size)[:, None]
    frequencies_x = np.fft.fftfreq(size)[None, :]
    radius = np.sqrt(frequencies_x * frequencies_x + frequencies_y * frequencies_y)
    shaped = np.fft.ifft2(np.fft.fft2(white) * radius).real
    ranks = np.empty_like(shaped)
    ranks.ravel()[np.argsort(shaped, axis=None)] = np.linspace(-0.5, 0.5, shaped.size)
    return ranks.astype(np.float32)[..., None]


def _to_rgb48_bytes(image: FloatImage, noise: FloatImage, frame_number: int) -> bytes:
    bt709 = np.clip(linear_to_bt709(image), 0.0, 1.0)
    height, width, _ = bt709.shape
    phase_y = (frame_number * 17) % noise.shape[0]
    phase_x = (frame_number * 29) % noise.shape[1]
    shifted = np.roll(noise, shift=(phase_y, phase_x), axis=(0, 1))
    tiled = np.tile(
        shifted,
        (math.ceil(height / noise.shape[0]), math.ceil(width / noise.shape[1]), 1),
    )[:height, :width]
    dithered = np.clip(bt709 + tiled / 1023.0, 0.0, 1.0)
    return np.rint(dithered * 65535.0).astype("<u2", copy=False).tobytes()


def _encoder_command(settings: RenderSettings, output_dir: Path) -> list[str]:
    dimensions = f"{settings.size}x{settings.size}"
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgb48le",
        "-video_size",
        dimensions,
        "-framerate",
        str(settings.fps),
        "-i",
        "pipe:0",
        "-map",
        "0:v:0",
        "-an",
        "-c:v",
        "libx265",
        "-preset",
        "slow",
        "-crf",
        "14",
        "-pix_fmt",
        "yuv420p10le",
        "-tag:v",
        "hvc1",
        "-x265-params",
        "colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-movflags",
        "+faststart",
        "-y",
        str(output_dir / "blue-skies-smooth-10bit.mp4"),
        "-map",
        "0:v:0",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "16",
        "-pix_fmt",
        "yuv420p",
        "-x264-params",
        "colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-movflags",
        "+faststart",
        "-y",
        str(output_dir / "blue-skies-smooth.mp4"),
    ]


def _write_frames(
    stream: ByteWriter,
    ordered_sources: list[Source],
    frame_counts: tuple[int, ...],
    settings: RenderSettings,
) -> int:
    if len(ordered_sources) < 4:
        raise ValueError("Continuous spline rendering requires at least four sources")

    noise = _blue_noise()
    frame_number = 0
    pair_count = settings.preview_pairs or len(ordered_sources)
    controls = (
        prepare_frame(ordered_sources[-1], settings.size),
        prepare_frame(ordered_sources[0], settings.size),
        prepare_frame(ordered_sources[1], settings.size),
        prepare_frame(ordered_sources[2], settings.size),
    )

    for pair_index in range(pair_count):
        count = frame_counts[pair_index]
        for local_frame in range(count):
            position = local_frame / count
            image = interpolate(controls, position)
            stream.write(_to_rgb48_bytes(image, noise, frame_number))
            frame_number += 1
        if pair_index + 1 < pair_count:
            incoming_index = (pair_index + 3) % len(ordered_sources)
            controls = (
                controls[1],
                controls[2],
                controls[3],
                prepare_frame(ordered_sources[incoming_index], settings.size),
            )
    return frame_number


def render_video(
    sources: list[Source], tour: Tour, output_dir: Path, settings: RenderSettings
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    ordered_sources = [sources[index] for index in tour.indices]
    counts = duration_frames(tour.distances_to_next, settings)
    command = _encoder_command(settings, output_dir)
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    if process.stdin is None:
        raise RuntimeError("ffmpeg did not expose stdin")
    try:
        frame_count = _write_frames(process.stdin, ordered_sources, counts, settings)
    finally:
        process.stdin.close()
    return_code = process.wait()
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, command)
    return frame_count


def write_order_manifest(
    sources: list[Source], tour: Tour, destination: Path, settings: RenderSettings
) -> None:
    counts = duration_frames(tour.distances_to_next, settings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "position",
                "asset_uuid",
                "date_created",
                "original_filename",
                "source_path",
                "distance_to_next",
                "transition_seconds",
            ]
        )
        for position, source_index in enumerate(tour.indices):
            source = sources[source_index]
            writer.writerow(
                [
                    position,
                    source.uuid,
                    source.date_created,
                    source.original_filename,
                    source.path,
                    f"{tour.distances_to_next[position]:.8f}",
                    f"{counts[position] / settings.fps:.6f}",
                ]
            )
