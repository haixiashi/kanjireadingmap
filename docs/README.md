# Kanjimap

Kanjimap is a dependency-free static page that shows a large map of Japanese
kanji readings as a 44×46 grid. The user-facing artifact is a single generated
[`index.html`](../index.html) file. The source of truth for the content is
[`src/data.json`](../src/data.json), and the app logic lives in
[`src/kanjimap.js`](../src/kanjimap.js) and [`src/styles.css`](../src/styles.css).

The design goal is simple:

- Ship one self-contained HTML file.
- Keep the editable source readable.
- Compress the generated artifact aggressively.
- Make the build reproducible from `src/data.json`.

## Project Shape

At a high level, the repo has three layers:

1. **Canonical data**: `src/data.json` contains the grid contents in display
   order.
2. **Human-edited app source**: `src/kanjimap.js` and `src/styles.css` define
   the UI and runtime decoder.
3. **Generated artifact**: `index.html` contains the inline bootstrap, the
   encoded data stream, and the compressed JS/CSS payload.

The build step computes probability models from the snapshot, rewrites the
readable JS into a compressed form, encodes the data stream, and assembles the
final single-file page.

## Working On The Project

Most edits should follow one of these paths:

- **UI change**: edit `src/kanjimap.js` or `src/styles.css`, then rebuild.
- **Data/content change**: edit or regenerate `src/data.json`, then rebuild and
  verify.
- **Encoding/build change**: update the Python tooling, then rebuild and verify.

### Normal commands

```bash
# Rebuild the snapshot from the dictionary sources
PYTHONPATH=tools python3 tools/rebuild_snapshot.py

# Rebuild the generated single-file app
PYTHONPATH=tools python3 tools/build.py

# Verify that index.html decodes back to src/data.json
python3 tools/verify_data.py
```

## Repo Guide

### Main files

- `src/data.json`
  Canonical grid data. This is the authoritative content source.

- `src/kanjimap.js`
  Readable application source. It contains the runtime decoder, table builder,
  and interaction logic.

- `src/styles.css`
  Readable stylesheet source. Inlined and minified into the generated page.

- `index.html`
  Generated single-file output. Do not hand-edit unless you are debugging the
  generated artifact itself.

- `build/kanjimap_processed.js`
  Minified payload emitted by the build for inspection. Useful when debugging
  placeholder replacement, renaming, or output size.

### Tooling

- `tools/build.py`
  Produces `index.html` from the readable source and snapshot data.

- `tools/verify_data.py`
  Decodes the generated `D` stream exactly as the page does and checks that the
  result matches `src/data.json`.

- `tools/reencode_bac.py`
  Encodes the snapshot into the arithmetic-coded `D` stream and validates its
  own round-trip.

- `tools/reencode_da.py`
  Base-93 transport codec used by the builder and verifier.

- `tools/rebuild_snapshot.py`
  Rebuilds `src/data.json` from the dictionary sources and normalizes reading
  choices, tiers, and sort order.

- `tools/resort_by_reading.py`
  Core scoring logic for assigning reading frequency.

- `tools/expand_entries.py`
  Data-maintenance helper for adding entries and handling archaic variants.

## Core Invariants

These matter more than individual helper names:

- `src/data.json` is the content source of truth.
- `index.html` must be reproducible from source.
- The browser decoder and `tools/verify_data.py` must stay byte-for-byte
  compatible with the current encoded format.
- The grid shape is fixed at 44 rows × 46 columns.
- Each cell is keyed in the snapshot as `row_kana + "+" + col_kana`.
- Entries remain in display order inside each cell.
- Tier values are non-increasing within a cell.
- The generated page must remain dependency-free and self-contained.

If you change the encoding, bit order, byte order, or model layout, update both
the runtime decoder and the verifier in the same change.

## Grid Model

The visual grid has 2,024 cells:

- 44 row headers for the first kana of a reading.
- 46 columns total.
- Column 0 is the one-kana header column.
- Columns 1–45 correspond to the second kana.

The kana ordering used for the grid is itself encoded in the data stream rather
than hardcoded in the generated payload.

## Snapshot Format

`src/data.json` stores the canonical content keyed by cell position:

```json
{
  "あ+": ["5有あ|る"],
  "あ+い": ["4愛アイ", "3藍アイ"]
}
```

Each entry string has this format:

```text
<tier><kanji><reading>[|<okurigana>]
```

Examples:

- `5有あ|る` = tier 5, kanji `有`, reading `あ`, okurigana `る`
- `4愛アイ` = tier 4, kanji `愛`, on-yomi `アイ`

At runtime each decoded entry becomes:

```js
[kanji, reading, tier, okurigana, isOn]
```

## Runtime Architecture

The app is easier to understand in three blocks:

### 1. Decode pipeline

The generated page boots a tiny inline loader that:

- decodes the compressed payload from base-93,
- inflates it with `DecompressionStream("deflate-raw")`,
- evaluates the minified payload.

Inside the payload, the runtime decoder:

- decodes the arithmetic-coded `D` stream,
- reconstructs the kanji table,
- reconstructs the kana probability table and grid kana order,
- decodes cell entries on demand as the table is built.

### 2. Table rendering

The table builder walks the full grid, decodes each cell, renders ruby text for
each entry, and stores the decoded entries on the `td` element so the UI layer
can reuse them without reparsing.

### 3. Interaction layer

The page supports:

- filtering by both / kun / on readings,
- light and dark themes,
- pan and zoom,
- hover/tap detail cards,
- adaptive font scaling and clipping for dense cells.

## Build Pipeline

`tools/build.py` does four conceptually important things:

1. Compute probability models from `src/data.json`.
2. Replace symbolic placeholders in the readable JS with concrete model values.
3. Minify and compress the payload.
4. Encode the snapshot into the `D` stream and assemble `index.html`.

The builder also writes `build/kanjimap_processed.js` so the generated payload
can be inspected without going through decompression.

## Verification

`tools/verify_data.py` is the integrity check for the whole format.

It does not merely inspect metadata. It:

- reads the `D` string from `index.html`,
- decodes it with the same base-93 and bit-order semantics as the browser,
- replays the arithmetic decoder,
- reconstructs the grid contents,
- compares every decoded entry against `src/data.json`.

If the verifier fails, assume the generated artifact, runtime decoder, and
Python tooling are out of sync until proven otherwise.

## Data Sources

The snapshot maintenance scripts use two external dictionaries stored in
`data/`:

- `data/kanjidic2.xml`
  Sole source of kanji readings.

- `data/JMdict_e.xml`
  Used only for frequency scoring and tier assignment.

These files are gitignored. Download them from:

- https://www.edrdg.org/wiki/index.php/KANJIDIC_Project
- https://www.edrdg.org/wiki/index.php/JMdict-EDICT_Dictionary_Project

## Snapshot Rebuild Logic

`tools/rebuild_snapshot.py` applies the main content-normalization rules:

1. Remove readings not supported by KANJIDIC2.
2. Pick the best reading for each kanji/cell combination.
3. Recompute tiers from JMdict-derived frequency scores.
4. Re-sort entries within each cell.

Two scoring choices matter:

- **Max-per-word scoring**: a `(kanji, reading)` pair gets the score of its
  best matching JMdict word, not a sum across all words.
- **Primary-form bias**: alternate spellings do not inherit the primary kanji
  form's score automatically.

## Encoding Reference

Most day-to-day work does not require touching the codec. This section is only
for format changes or debugging.

### High-level structure

The generated page contains two encoded strings:

- `F`: deflate-raw compressed JS/CSS payload, transported via base-93.
- `D`: arithmetic-coded data stream for the grid content.

### `D` stream sections

The `D` stream is decoded sequentially in one arithmetic-decoder state:

1. **Kanji table**
   Delta-encoded codepoints beginning at `一` (`U+4E00`).

2. **Kana probability table**
   An 82-symbol cumulative model covering kana offsets from `あ` onward,
   used for kun-yomi extra kana and okurigana.

3. **Grid kana layout**
   The 45 kana used to define row and column order.

4. **Cell contents**
   Presence flag, kanji grouping, on/kun flag, tier deltas, reading variants,
   on-yomi extra-kana flag and symbol, kun-yomi extra kana, and okurigana.

### Important implementation constraints

- The arithmetic coder uses 32-bit precision.
- Symbol models are 999-scale cumulative tables.
- Uniform decoding uses exact range subdivision, not power-of-two rounding.
- Byte packing is LSB-first within each byte.
- The stored byte stream is reversed so the JS decoder can consume it with
  `pop()`.
- The runtime bit reader treats reads past the end of `D` as zero bytes, so the
  arithmetic decoder sees the usual zero-extended tail during final
  normalization.
- The Python verifier must mirror those exact transport details.
- On-yomi is encoded as katakana and never carries okurigana.
- First-column on-yomi has no extra kana; two-kana on-yomi has at most one
  extra kana.
- That single on-yomi extra kana uses a dedicated tiny model
  (`M_ON_EXTRA` + `M_ON_KANA`), while the 82-symbol kana table is now
  effectively a kun-kana model.

## Notes

- Avoid calling `.strip()` on the `D` string. Space is a valid base-93 digit
  and may legitimately appear at either end.
- When editing docs or tooling, prefer explaining project invariants and
  workflow over copying the current minified implementation structure.
