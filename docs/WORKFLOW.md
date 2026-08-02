# Blue Skies production workflow

This document records how the Blue Skies animation was selected, exported, ordered,
interpolated, encoded, reviewed, and delivered. It is the authoritative workflow for
future renders from this repository.

The export program is named **osxphotos** and its export subcommand is
`osxphotos export`. “osxexport” is informal shorthand, not the executable name.
The authoritative option reference is the
[osxphotos export CLI documentation](https://rhettbull.github.io/osxphotos/cli/#export).

## 1. Curated selection

The first pass scanned 15,730 visible assets in the Photos library. Local Photos
derivatives were used for fast image scoring; five iCloud-only candidates were fetched
as previews and reviewed separately. Candidates favored sky classifications, broad
color fields, low edge density, and low luminance variation. Human contact-sheet review
then removed landscapes, buildings, utility lines, trees, near-black frames, aircraft,
and ambiguous airborne silhouettes.

The repository contains the resulting 101-photo selection:

- `selection.csv` is the authoritative asset manifest and retains creation date,
  original filename, UUID, camera metadata, and the scan metrics.
- `selection-uuids.txt` is the exact UUID input for `osxphotos`.

If the selection changes, update both files in the same commit and verify their UUID
sets are identical:

```bash
awk -F ',' 'NR > 1 { id=$3; gsub(/"/, "", id); print id }' selection.csv \
  | diff -u - selection-uuids.txt
```

## 2. Export full-resolution originals

Do not render from `Photos Library.photoslibrary/resources/derivatives/masters`.
Those cached derivatives were only 360–480 pixels for this selection. Export the
originals with `osxphotos` so unavailable local assets can be retrieved from iCloud.

### One-photo compatibility test

Create the destination first—`osxphotos` requires it to exist—and test one selected
asset before the full run. This is especially important on newly released macOS
versions, which may have received less osxphotos testing:

```bash
mkdir -p source-test

uvx --python 3.13 osxphotos export source-test \
  --library "/Users/bryan/Pictures/Photos Library.photoslibrary" \
  --uuid-from-file selection-uuids.txt \
  --download-missing \
  --skip-edited \
  --skip-live \
  --skip-bursts \
  --skip-raw \
  --filename "{uuid}" \
  --limit 1 \
  --update \
  --report source-test-report.csv
```

The original test exported a 3264×2448 sRGB JPEG instead of its 360×480 cache.
Inspect the test result with:

```bash
sips -g pixelWidth -g pixelHeight -g bitsPerSample -g profile source-test/*
```

### Full resumable export

```bash
mkdir -p sources

uvx --python 3.13 osxphotos export sources \
  --library "/Users/bryan/Pictures/Photos Library.photoslibrary" \
  --uuid-from-file selection-uuids.txt \
  --download-missing \
  --skip-edited \
  --skip-live \
  --skip-bursts \
  --skip-raw \
  --filename "{uuid}" \
  --update \
  --report export.csv
```

Important flag behavior:

- `--uuid-from-file` limits export to the curated set.
- `--download-missing` asks Photos to obtain originals that are absent locally.
- `--update` stores state in `sources/.osxphotos_export.db`, making the same command
  resumable.
- `--skip-edited` exports the camera originals instead of rendered Photos edits.
- `--skip-live`, `--skip-bursts`, and `--skip-raw` omit associated media not consumed
  by this still-image renderer. A standalone RAW photo is not skipped.
- `--filename "{uuid}"` prevents camera-filename collisions and lets the renderer map
  every file back to the manifest without heuristics.

Never add `--preview-if-missing`. A preview is not a full-resolution original. If the
original is gone from both iCloud and its referenced source, stop and report the missing
asset rather than silently lowering quality.

The successful production export yielded 99 HEIC and 7 JPEG originals with zero missing
or errored assets. Validate every run:

```bash
awk -F ',' 'NR > 1 { missing += $13; error += $14 }
  END { print "missing=" missing, "error=" error }' export.csv

find sources -maxdepth 1 -type f \
  | rg -i '\.(jpe?g|heic|heif|png|tiff?|dng|avif)$' \
  | wc -l
```

The source directory may contain an original removed during later curation. The renderer
uses only UUIDs present in `selection.csv`; an extra unreferenced export is harmless.

## 3. Install the locked renderer

```bash
uv sync --frozen
uv run ruff format --check src tests
uv run ruff check src tests
uv run ty check
uv run pytest -q
```

The runtime dependency set is intentionally small: NumPy and Pillow. FFmpeg handles
JPEG and Apple grid-based HEIC decoding, including autorotation. The project does not
require a neural model and does not invent clouds or objects.

## 4. How the animation is constructed

### Decode and normalize

Each original is decoded through an FFmpeg complex filter graph to 16-bit RGB,
autorotated, center-cropped to a square, and converted from sRGB to linear light. A
complex graph is required because Apple HEIC files may be tiled grid images; attaching a
simple `-vf` graph to those inputs fails.

### Find a smooth cyclic order

For every source, the renderer builds a descriptor from:

- a 64×64 linear-light decode converted to OKLab;
- a weighted 4×4 spatial OKLab field;
- global OKLab mean and spread; and
- luminance-gradient and texture measurements.

It computes all pairwise descriptor distances, evaluates nearest-neighbor cyclic tours
from every starting asset, and applies strictly decreasing 2-opt improvements. The cycle
is rotated back to the first manifest asset so output is deterministic. The current
production tour reduced total perceptual cycle distance from `77.638227793557` to
`20.909465714808`, a 73.07% reduction compared with chronological order.

### Assign transition durations

Perceptual distances are mapped between their 10th and 90th percentiles. Quintic
smootherstep is used only to assign each segment between 0.45 and 1.05 seconds:

```text
smootherstep(t) = 6t^5 - 15t^4 + 10t^3
```

Similar skies pass quickly; larger color or texture changes receive more time. Within a
segment, the spline parameter advances linearly. There is deliberately no per-photo
temporal easing, endpoint hold, or arrival phase.

### Interpolate color and detail separately

Each full-resolution frame is decomposed into three spatial bands:

1. a low-frequency base reconstructed at 1/16 resolution;
2. a mid-frequency band between 1/4 and 1/16 resolution; and
3. the remaining high-frequency detail.

Every segment is evaluated from four cyclically adjacent photographs with periodic
uniform cubic B-spline weights:

```text
w0 = (1-t)^3 / 6
w1 = (3t^3 - 6t^2 + 4) / 6
w2 = (-3t^3 + 3t^2 + 3t + 1) / 6
w3 = t^3 / 6
```

The low-frequency fields use those weights directly in OKLab. Mid-frequency weights are
squared and renormalized; fine-detail weights are cubed and renormalized. This keeps
texture concentrated around the central skies without sacrificing the spline's seamless
cyclic continuity.

Unlike an eased pairwise dissolve, the periodic spline is `C2` continuous and has a
nonzero tangent as it passes every source knot. It never stops to present a photograph as
a static endpoint; the result is one continuously swirling trajectory through the whole
selection.

The linear-light result is encoded with the BT.709 transfer function. A deterministic,
Fourier-shaped high-frequency dither is shifted each frame at approximately one 10-bit
quantization step to suppress banding in flat skies.

### Encode delivery formats

One stream of 16-bit RGB frames feeds both encoders:

- `blue-skies-smooth-10bit.mp4`: HEVC Main 10, `yuv420p10le`, CRF 14, BT.709;
- `blue-skies-smooth.mp4`: H.264, `yuv420p`, CRF 16, BT.709; and
- `blue-skies-smooth.gif`: 480×480 at 25 fps with a 256-color generated palette and
  Sierra dithering.

The 10-bit HEVC file is the quality master. The H.264 file is the compatibility copy.
GIF is only a portable preview because its palette cannot faithfully preserve all sky
gradients.

## 5. Preview, inspect, then render

Always render a bounded preview first:

```bash
uv run blue-skies render \
  --manifest selection.csv \
  --source-dir sources \
  --output-dir output/preview \
  --size 480 \
  --fps 30 \
  --preview-pairs 12 \
  --no-gif
```

Inspect:

- `output/preview/ordered-contact-sheet.png` for unwanted content;
- `output/preview/order.csv` for ordering and adaptive durations;
- `output/preview/render-report.json` for the optimized-distance reduction; and
- both MP4 previews for ghosting, stepping, hue detours, and banding.

Then render the final loop:

```bash
uv run blue-skies render \
  --manifest selection.csv \
  --source-dir sources \
  --output-dir output/swirl-final \
  --size 1080 \
  --fps 60
```

The delivered render contains 4,138 frames, runs for 68.966667 seconds, and continuously
circulates through all 101 selected photographs.

## 6. Final verification

Check frame count, dimensions, pixel format, frame rate, duration, and color metadata:

```bash
ffprobe -v error -select_streams v:0 \
  -show_entries \
stream=codec_name,width,height,pix_fmt,color_space,color_transfer,color_primaries,avg_frame_rate,nb_frames:format=duration,size \
  -of default=noprint_wrappers=1 \
  output/swirl-final/blue-skies-smooth-10bit.mp4
```

Expected master properties:

```text
codec_name=hevc
width=1080
height=1080
pix_fmt=yuv420p10le
color_space=bt709
color_transfer=bt709
color_primaries=bt709
avg_frame_rate=60/1
nb_frames=4138
duration=68.966667
```

The current production run compared its first and last compatibility frames and measured
45.58 dB PSNR. A low-resolution motion audit found nonzero movement through every one of
the 100 internal knots, with a median boundary-to-local-motion ratio of 1.000. Finally,
verify copied deliverables with SHA-256 rather than relying on filenames or timestamps.

## 7. Delivery and repository policy

The current files are copied to `/Users/bryan/Pictures/Sky Fade/` with the
`blue-skies-swirl-*` prefix. Previous experimental renders remain intact.

`sources/`, `output/`, export databases, and export reports are generated scratch data
and remain ignored. Commit code, tests, lockfile, the curated manifests, and workflow
documentation. The production implementation was committed as `2d49dbc`; subsequent
workflow changes should be committed separately after the validation commands above pass.
