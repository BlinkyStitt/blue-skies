# Blue Skies

Builds a seamless sky-photo loop using a deterministic photographic pipeline:

1. export the selected full-resolution originals from Photos with `osxphotos`;
2. find a short cyclic tour through a perceptual OKLab/spatial-texture descriptor;
3. assign more time to perceptually difficult transitions;
4. interpolate low-frequency color fields in OKLab;
5. dissolve mid- and high-frequency detail independently with quintic easing;
6. add deterministic high-frequency dither and encode 10-bit HEVC plus H.264.

The renderer does not synthesize objects or invent cloud motion. It is deliberately
faithful to the selected photographs.

## Requirements

- macOS Photos, for original export
- Python 3.12+
- `uv`
- `ffmpeg` and `ffprobe`

## Reproduce

```bash
uv sync

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

uv run blue-skies render \
  --manifest selection.csv \
  --source-dir sources \
  --output-dir output \
  --size 1080 \
  --fps 60
```

Use `--preview-pairs 12 --size 480 --fps 30` for a fast representative render.

`--uuid-from-file` limits the export to this curated set. `--download-missing`
asks Photos to retrieve unavailable originals from iCloud, while `--update`
keeps the export resumable. The UUID filename template makes source resolution
unambiguous even when several photographs share the same camera filename.

Do not add `--preview-if-missing` to this workflow: a cached preview is not a
genuine original. Treat any nonzero `missing` or `error` value in `export.csv`
as a source-quality failure. `osxphotos` documents limited testing on newer
macOS releases, so the command above was first verified with a one-photo export
before this set was exported.
