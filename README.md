# Blue Skies

Builds a seamless sky-photo loop using a deterministic photographic pipeline:

1. export the selected full-resolution originals from Photos with `osxphotos`;
2. find a short cyclic tour through a perceptual OKLab/spatial-texture descriptor;
3. assign more time to perceptually difficult transitions;
4. move continuously through four neighboring skies with a periodic cubic B-spline;
5. interpolate low-frequency color in OKLab and sharpen the spline weights for detail;
6. add deterministic high-frequency dither and encode 10-bit HEVC plus H.264.

The renderer does not synthesize objects or invent cloud motion. It is deliberately
faithful to the selected photographs.

See [the production workflow](docs/WORKFLOW.md) for selection provenance, the
resumable `osxphotos export` process, interpolation details, preview review, and
final validation. Repository agents must also follow `AGENTS.md`.

## Requirements

- macOS Photos, for original export
- Python 3.12+
- `uv`
- `ffmpeg` and `ffprobe`

## Optional error reporting

Blue Skies does not send telemetry unless `BLUE_SKIES_SENTRY_DSN` is set to an
HTTPS DSN. On macOS and Linux, reporting also requires `curl`. The SDK sends
unhandled CLI exceptions and error-level Python logs. It excludes exception/log
messages, source paths, photo contents and metadata, command arguments, local
variables, user/host details, attachments and breadcrumbs. It sends exception
classes and code module/function/line locations only. Tracing, profiling and
session reporting are not enabled.

Set `SENTRY_RELEASE` to the exact full Git commit for an installed wheel. A tracked source
checkout derives this revision from Git when the variable is absent. Untracked
source archives require an explicit revision. Reporting
rejects an invalid revision; it never substitutes `dev`. The release is
`blue-skies@<full commit>`. Keep the DSN in your launch environment, outside Git.

Uploads use the native SDK queue and rate limits with verified TLS, no redirects
or retries, a two-second absolute HTTP deadline, and a two-second shutdown flush.
The collector's response body is never stored or sent to another destination.
Reporting preserves the command's failure status and normal local traceback.

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

uv run blue-skies farcaster-gif \
  --source output/blue-skies-smooth.mp4 \
  --destination output/blue-skies-farcaster.gif
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

The normal render keeps the full-quality video and archival GIF outputs. The additional
Farcaster command preserves the full loop at 15 source fps with a 128-color ordered-dither
palette, then binary-searches for the largest even square dimension whose encoded file is
no more than 5,000,000 bytes.
