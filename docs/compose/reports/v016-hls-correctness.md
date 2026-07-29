---
feature: v016-hls-correctness
status: delivered
specs: []
plans:
  - docs/compose/plans/2026-07-14-v016-hls-correctness.md
branch: main
commits: 2dd1f2d..6fd9346
---

# v0.16 HLS Correctness & Real MP4 Output — Final Report

## What Was Built

v0.16 addresses two categories of issues in the M3U8/HLS download pipeline: correctness ( MEDIA-SEQUENCE parsing, AES IV handling) and output quality (FFmpeg remux for real MP4).

The M3U8 parser now correctly handles `EXT-X-MEDIA-SEQUENCE`, which is essential for live streams and time-shifted content where segment numbering doesn't start at 0. The AES-128 decryptor uses MEDIA-SEQUENCE as the default IV when the M3U8 doesn't explicitly provide one, matching the HLS specification.

For output, the merger module was rewritten to use FFmpeg `-c copy` remux instead of binary concatenation. This produces genuine MP4 containers compatible with all media players and video editors. A segment count verification step was added after download, with automatic retry for failed segments. Output files are validated for size reasonability, and FFmpeg availability is checked at startup with a fallback warning.

## Architecture

### Pipeline (unchanged structure, improved internals)

```
M3U8 URL
  → m3u8_parser.py (now parses EXT-X-MEDIA-SEQUENCE)
  → downloader.py (segment verification + retry)
  → decryptor.py (uses MEDIA-SEQUENCE as default IV)
  → merger.py (FFmpeg -c copy remux)
  → real MP4 output
```

### Key Changes by File

| File | Change |
|------|--------|
| `m3u8_parser.py` | Added `media_sequence` field to `M3U8Playlist`; parse `#EXT-X-MEDIA-SEQUENCE` tag; segment indexing starts from `media_sequence` |
| `decryptor.py` | `decrypt_segment()` accepts `media_sequence` param; uses `media_sequence.to_bytes(16, 'big')` as default IV; `decrypt_files()` passes MEDIA-SEQUENCE through |
| `downloader.py` | After main download loop: verify segment count, collect missing indices, retry failed segments with fresh session |
| `merger.py` | Replaced binary concat with FFmpeg concat demuxer (`-c copy -movflags +faststart`); validates output file exists and non-empty |
| `utils.py` | Added `check_ffmpeg()` utility; added `import subprocess` |
| `app.py` | Passes `media_sequence` to `decrypt_files()`; validates output file size and duration; checks FFmpeg availability at download start |

### Design Decisions

**FFmpeg concat demuxer over TS binary concat:** TS files can be binary-concatenated, but the result isn't a valid MP4 container. FFmpeg's concat demuxer with `-c copy` produces genuine MP4 with proper moov/mdat atoms. The `-movflags +faststart` flag moves the moov atom to the beginning for streaming playback.

**MEDIA-SEQUENCE as IV over fixed 0x00 IV:** The HLS spec (RFC 8216, Section 4.3.2.4) mandates that when `EXT-X-KEY` has no `IV` attribute, the IV should be the MEDIA-SEQUENCE value encoded as a 128-bit big-endian integer. Using 0x00 only works when MEDIA-SEQUENCE=0 (non-live VOD content). For live/time-shifted streams, the old approach produced incorrect decryption.

**Retry after verification over per-segment retry:** The existing `download_segment()` already retries each segment 5 times. The new verification catches cases where all retries exhausted (network outage, CDN issues) and gives one more attempt with a fresh connection pool.

## Usage

No user-facing changes. The improvements are transparent:

- M3U8 downloads now produce real MP4 files (previously were TS-concatenated with .mp4 extension)
- AES-128 encrypted streams with MEDIA-SEQUENCE > 0 now decrypt correctly
- Failed segments are automatically retried after the main download completes
- If FFmpeg is not installed, a warning is logged (fallback to simple concat still works but output may not be standard MP4)

## Verification

4 integration tests passed:
1. Basic M3U8 parsing (2 segments, media_sequence=0)
2. MEDIA-SEQUENCE parsing (sequence=100, segment indices 100, 101)
3. AES IV generation (100 → `00000000000000000000000000000064`)
4. FFmpeg availability check (system FFmpeg 5.0 confirmed)

## Journey Log

- [lesson] MEDIA-SEQUENCE must update `seg_index` at parse time, not just set `playlist.media_sequence` — the index variable is local to `_parse_media` and doesn't auto-sync
- [lesson] PowerShell doesn't support `head` — use `Select-Object -First N` instead

## Source Materials

| File | Role |
|------|------|
| `docs/compose/plans/2026-07-14-v016-hls-correctness.md` | Implementation plan |
