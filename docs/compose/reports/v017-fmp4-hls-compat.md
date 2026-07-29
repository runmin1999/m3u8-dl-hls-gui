---
feature: v017-fmp4-hls-compat
status: delivered
specs: []
plans:
  - docs/compose/plans/2026-07-14-v017-fmp4-hls-compat.md
branch: main
commits: c1f67a0..792b5fe
---

# v0.17 fMP4 & Extended HLS Compatibility — Final Report

## What Was Built

v0.17 adds support for modern HLS streams that use fragmented MP4 (fMP4) instead of MPEG-TS segments. This includes parsing `EXT-X-MAP` (init segments), `EXT-X-BYTERANGE` (byte-range segmentation), extracting audio/subtitle tracks from master playlists, and using FFmpeg to mux separate audio/video tracks into a single MP4 file.

The download pipeline now auto-detects fMP4 vs TS format and routes to the appropriate flow: fMP4 streams download an init segment + media segments and concatenate them; TS streams use the existing FFmpeg remux path. Audio track selection is available in the UI when the master playlist contains multiple audio tracks.

## Architecture

### Pipeline (extended)

```
Master M3U8 URL
  → m3u8_parser.py (parses STREAM-INF, MEDIA, MAP, BYTERANGE)
  → Select resolution + audio track
  → Media Playlist URL
  → m3u8_parser.py (parses segments, init segment, encryption)
  → Detect fMP4 vs TS (by init_segment_url or .m4s extension)
  ↓
  fMP4 path:                      TS path:
    download_init_segment()         download_all()
    download_all()                  decrypt_files()
    decrypt_files()                 merge_to_ts() → FFmpeg remux
    merge_fmp4() → binary concat
  ↓
  Final MP4 output
```

### Key Changes by File

| File | Change |
|------|--------|
| `m3u8_parser.py` | Added `init_segment_url`, `init_segment_byterange` to M3U8Playlist; added `byterange` to Segment; added `track_type`, `language`, `default` to StreamInfo; added `audio_tracks`, `subtitle_tracks` to M3U8Playlist; parse `#EXT-X-MAP`, `#EXT-X-BYTERANGE`; extract audio/subtitle tracks from `#EXT-X-MEDIA` |
| `downloader.py` | Added `download_init_segment()` for fMP4 init segments; added BYTERANGE Range header support to `download_segment()` |
| `merger.py` | Added `merge_fmp4()` for binary concatenation of init + media segments; added `mux_audio_video()` for FFmpeg audio/video mux |
| `app.py` | Added fMP4 format detection; dual download flow (fMP4 vs TS); audio track selection UI; audio/subtitle track extraction from master playlist; persistence of audio track selection |

### Design Decisions

**fMP4 detection by init_segment_url presence:** If `EXT-X-MAP` is present, the stream is fMP4. As a fallback, we also check if segment URLs end with `.m4s`. This covers both cases: streams that use EXT-X-MAP and streams that implicitly use fMP4 via file extensions.

**Binary concat for fMP4 (not FFmpeg remux):** fMP4 segments are designed to be concatenated — each media segment is a self-contained moof+mdat atom pair, and the init segment provides the moov atom. Binary concatenation produces a valid fMP4 file. FFmpeg remux is only needed for TS→MP4 conversion.

**Audio track as UI dropdown (not per-task):** The audio track selector is a global form element, updated when a new M3U8 is parsed. This keeps the UI simple — the selection applies to the next download task. Per-task audio track storage is supported for persistence/resume.

## Usage

**fMP4 streams:** Automatically detected. No user action needed — the app detects `EXT-X-MAP` or `.m4s` extensions and switches to fMP4 mode.

**Audio track selection:** When a master playlist contains multiple audio tracks, the "音频轨道" dropdown is populated. Select a track before clicking "开始下载".

**BYTERANGE streams:** Automatically handled. Segments with `EXT-X-BYTERANGE` use HTTP Range headers for partial downloads.

## Verification

8 integration tests passed:
1. EXT-X-MAP parsing (init URL resolution)
2. EXT-X-BYTERANGE parsing (byte range + shared URL)
3. Audio/subtitle track extraction (2 audio, 1 subtitle)
4. MEDIA-SEQUENCE with fMP4 (combined parsing)
5. FFmpeg availability
6. Segment byterange field
7. StreamInfo track fields
8. M3U8Playlist new fields

## Journey Log

- [lesson] fMP4 init segment must be downloaded separately from media segments — download_all doesn't handle init segments
- [lesson] BYTERANGE segments share the same URL — the parser must track last_seg_url and reuse it
- [lesson] Audio track dropdown needs to be updated dynamically when a new M3U8 is parsed — static values don't work for multi-track streams

## Source Materials

| File | Role |
|------|------|
| `docs/compose/plans/2026-07-14-v017-fmp4-hls-compat.md` | Implementation plan |
