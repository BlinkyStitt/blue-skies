from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from .media import load_sources, write_contact_sheet
from .ordering import build_tour, cycle_length, pairwise_distances
from .render import RenderSettings, render_video, write_order_manifest


def _make_gif(source: Path, destination: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vf",
            "fps=25,scale=480:480:flags=lanczos,split[a][b];"
            "[a]palettegen=max_colors=256:stats_mode=diff[p];"
            "[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle",
            "-loop",
            "0",
            "-y",
            str(destination),
        ],
        check=True,
    )


def render(arguments: argparse.Namespace) -> None:
    sources = load_sources(arguments.manifest.resolve(), arguments.source_dir.resolve())
    tour, descriptors = build_tour(sources)
    distances = pairwise_distances(descriptors)
    chronological_distance = cycle_length(list(range(len(sources))), distances)

    settings = RenderSettings(
        size=arguments.size,
        fps=arguments.fps,
        min_duration=arguments.min_duration,
        max_duration=arguments.max_duration,
        preview_pairs=arguments.preview_pairs,
    )
    output_dir = arguments.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    write_order_manifest(sources, tour, output_dir / "order.csv", settings)
    ordered_sources = [sources[index] for index in tour.indices]
    write_contact_sheet(ordered_sources, output_dir / "ordered-contact-sheet.png")

    report = {
        "source_count": len(sources),
        "chronological_cycle_distance": chronological_distance,
        "optimized_cycle_distance": tour.total_distance,
        "distance_reduction_fraction": 1.0 - tour.total_distance / chronological_distance,
        "size": settings.size,
        "fps": settings.fps,
        "preview_pairs": settings.preview_pairs,
    }
    (output_dir / "render-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )

    frame_count = render_video(sources, tour, output_dir, settings)
    report["rendered_frames"] = frame_count
    (output_dir / "render-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )

    if not arguments.no_gif:
        _make_gif(output_dir / "blue-skies-smooth.mp4", output_dir / "blue-skies-smooth.gif")


def main() -> None:
    parser = argparse.ArgumentParser(description="Render smooth sky-photo transitions")
    subparsers = parser.add_subparsers(dest="command", required=True)
    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("--manifest", type=Path, required=True)
    render_parser.add_argument("--source-dir", type=Path, required=True)
    render_parser.add_argument("--output-dir", type=Path, required=True)
    render_parser.add_argument("--size", type=int, default=1080)
    render_parser.add_argument("--fps", type=int, default=60)
    render_parser.add_argument("--min-duration", type=float, default=0.45)
    render_parser.add_argument("--max-duration", type=float, default=1.05)
    render_parser.add_argument("--preview-pairs", type=int)
    render_parser.add_argument("--no-gif", action="store_true")
    render_parser.set_defaults(handler=render)
    arguments = parser.parse_args()

    for executable in ("ffmpeg", "ffprobe"):
        if shutil.which(executable) is None:
            parser.error(f"Required executable not found: {executable}")
    arguments.handler(arguments)


if __name__ == "__main__":
    main()
