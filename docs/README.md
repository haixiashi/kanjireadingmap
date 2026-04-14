# Kanjimap

A single-file HTML page (`index.html`) that displays a 44×46 grid of
Japanese kanji organized by reading (pronunciation). All data is
serialized inline as encoded strings. The page has no external dependencies. JS and CSS are gzip-compressed and decompressed
at runtime via `DecompressionStream`.

## Grid Structure

- **44 rows** × **46 columns** = 2,024 cells
- Rows correspond to the first kana of the reading (あ–わ, 44 of 45 kana)
- Columns: first column is a "header" (readings starting with just that
  kana), remaining 45 columns correspond to the second kana (あ–ん)
- The 45 kana used for grid row/column layout are decoded from the D
  stream (Section 3) using `U(82)` offsets from `H` (0x3042 = あ)

## Data Encoding

### D String (Combined Data)

All data is encoded in a single arithmetic-coded stream `D`, packed
into base-93 via rANS streaming codec.

**Base-93 alphabet**: U+0020–U+007E excluding `"` and `\` (93 chars).
Char-to-digit: `(charCode+26)*58/59-57|0`

**Base-93 decoder** `B(s)`: rANS-style streaming, no BigInt. Reads
string left-to-right, sentinel-terminated (encoder starts with state=1):
- Refill: `v = v*93 + nextDigit()` while `v < 2^24`
- Extract byte: `v & 255`, then `v >>= 8`
- Loop: refill → extract → repeat while `v > 1`
- State stays under 2^31 (safe for JS bitwise ops)
- Encoding efficiency: 8/log2(93) ≈ 1.2234 chars/byte (theoretical optimum)
- Bootstrap uses `B` for F (byte array for gzip decompression)
- Payload wraps `B(D)` in a generator that yields bits MSB-first from
  each byte, feeding the arithmetic decoder without intermediate strings

**Arithmetic decoder** (32-bit precision, inside DC IIFE):
- State: `a` (low), `d` (high), `e` (value), all 32-bit
- Constants: `T=2**31` (TOP), `Q=T/2` (QUARTER), `M=T*2` (MODULUS)
- `W()`: normalization — shifts out resolved bits, reads new bits
- `Z(c)`: decode symbol using cumulative frequency array `c` (999-scale,
  inner values only — implicit 0 and 999). Uses step-based lookup
  matching encoder boundaries.
- `U(n)`: decode uniform symbol 0..n-1. Uses `q=Math.trunc(r/n)` for
  single-step range subdivision, matching the encoder.

### D Stream Layout

The D stream contains three sections, decoded sequentially from a
single arithmetic-coded bitstream (no re-initialization between sections):

**Section 1: KT (Kanji Table)** — kanji as delta-encoded codepoints
- KL-1 deltas using Exponential-Golomb variant:
  `q = 2 << Z(KD); delta = U(q) + q - 1`
  8 doubling cases: q=2 (delta 1–2) through q=256 (delta 255–510)
  Arithmetic-coded case selector replaces fixed-length prefix
- First char is `一` (U+4E00). Each subsequent = previous + delta.

**Section 2: Kana probability table** (82 symbols in codepoint order)
- 81 k² deltas: `U(14)` × 81 — each value k is squared to get the delta,
  building the 999-scale prob table covering all 82 kana offsets (U+3042–U+3093)
- k² approximation saves ~27 bytes vs raw deltas (U(171)) with minimal
  compression loss; the 82nd symbol gets the remainder (999 − sum)
- No explicit kana code list needed; symbol index = codepoint offset

**Section 3: KN (kana row/col mapping)** — 45 kana for grid layout
- First kana is always あ (H = 0x3042)
- 44 deltas: `U(4)` × 44 — each delta minus 1 (deltas 1–4, encoded as 0–3)

**Section 4: Cell data** (read per cell, row 0–43, col 0–45)

1. **cell_present**: `Z(CP)` → 0=empty, 1=non-empty (skipped for
   first column, which is always non-empty)
2. If non-empty, loop over kanji groups:
   a. **kanji_type**: position-dependent model, KT0 conditioned on `pt`
      - First in group: `Z(KP[pt-1])` → 0=kanji, 1=end of cell
        (pt=5: 99.3% kanji, pt=1: 47%)
      - Subsequent: `Z(KT1)` → 0=kanji (27%), 1=end of group
      - If kanji: `U(KL)` → index into KT table
   b. **on_kun**: `Z(OK)` → 0=kun-yomi, 1=on-yomi
   c. **tier**: `pt` starts at 5; `pt -= Z(...TP[pt-1])`
      Per-pt probability tables (TP[0] is empty for pt=1, no-op)
   d. **variant**: `d1=Z(D1K|D1O)` (conditional on on/kun), then
      `d2=Z(D2|d1)-1` (conditional on d1)
   e. **Furigana prefix**: reconstructed from cell position + on/kun + variant
   f. **Extra reading**: loop `Z(EF)` → 0=done, 1=more char
      - **kana**: `Z(...KA)+H+f` — symbol index = kana offset, + H (0x3042)
      - No lookup table needed; Z() result is the codepoint offset directly
   g. **Okurigana** (kun-yomi only): loop `Z(OF)` → 0=done, 1=more char
      - Same: `Z(...KA)+H` (no ko offset for okurigana)
   h. Return entry as array: `[kanji, reading, tier, okurigana, is_on]`

### Probability Models (999-scale cumulative frequency arrays)

All models use total=999. The JS code inlines the inner boundaries
(implicit 0 at start and 999 at end) directly at each call site
as variadic arguments, e.g. `Z(555)`. The `Z` function uses rest
parameters (`Z=(...c)=>`) to collect them into an array.

| Name | Placeholder | Symbols |
|------|-------------|---------|
| KD_CASE (delta bucket) | `Z(KD)` | 8 exp-Golomb cases (fixed model) |
| CP (cell_present) | `Z(CP)` | empty / non-empty |
| KT0 (kanji_type first) | `Z(KP[pt-1])` | kanji / end-of-cell (per-pt) |
| KT1 (kanji_type subseq) | `Z(K1)` | kanji / end-of-group |
| OK (on_kun) | `Z(OK)` | kun / on |
| TP (tier delta, per-pt) | `Z(...TP[pt-1])` | delta from pt (per-pt) |
| D1K/D1O (d1) | `Z(x?DO:DK)` | 0 / 1 (conditional on on/kun) |
| D0/D1 (d2) | `m?Z(D1):Z(D0)` | -1 / 0 / 1 (conditional on d1) |
| EF (extra_rd_flag) | `Z(EF)` | done / more |
| OF (okuri_flag) | `Z(OF)` | done / more |
| KA (kana) | `Z(...KA)` | 82-symbol table (stream-decoded) |

All model values except KD_CASE are computed from snapshot data by
`build.py` and inlined into the JS. See `kanjimap_processed.js` for
current values.

### Kana Encoding

All 82 possible kana codepoints (U+3042–U+3093) are covered by a
single probability model `Z(...KA)` where `KA` is an 81-entry
cumulative frequency table decoded from the D stream. The symbol
index directly equals the kana's codepoint offset from H (0x3042),
so no lookup array is needed. Unused kana get minimal probability (1/999).

The kana codepoint is `Z(...KA) + H + ko` where H=12354 (0x3042)
and ko=96 for on-yomi. The KN string (45 kana for the grid layout)
is also decoded from the D stream via `U(82)+H`.

### Variant Encoding

Offsets `d1` (first char, 0 or 1) and `d2` (rest, -1/0/1) are applied
to the cell's kana prefix for readings with dakuten/handakuten.
They are encoded as two separate fields with conditional probability:
1. `d1 = Z(x?DO:DK)` — conditional on on/kun
2. `d2 = (m?Z(D1):Z(D0)) - 1` — conditional on d1

The conditional tables capture the correlation (d1=1, d2=1 is nearly
impossible) without wasting bits on a joint 6-symbol table.

### Tier System

5 tiers assigned by JMdict word frequency (max-per-word scoring).
Archaic/rarely-used kanji forms (JMdict oK/rK/uk/arch/obs tags) are
excluded entirely.
- Tier 5 (j5): score ≥ 98 (~9%) — core readings
- Tier 4 (j4): score ≥ 92 (~19%) — very common
- Tier 3 (j3): score ≥ 49 (~32%) — common
- Tier 2 (j2): score ≥ 5 (~21%) — moderate
- Tier 1 (j1): score < 5 (~19%) — attested, low frequency

Within each cell, tiers are non-increasing (sorted by score descending).
Encoded as deltas from `pt` (starts at 5): `pt -= Z(...TP[pt-1])`.
Each pt level has its own probability table, exploiting the fact that
higher tiers have flatter delta distributions while lower tiers are
heavily skewed toward delta=0. The KT0 model (more groups vs
end-of-cell) is also conditioned on `pt` (99% more at pt=5, 47% at pt=1).

## Entry Format

Each decoded entry is a JS array: `[kanji, reading, tier, okurigana, is_on]`

Example: `['有', 'あ', 5, 'る', 0]` = kanji 有, reading あ, tier 5,
okurigana る, kun-yomi. MP() renders this directly as a DOM span with
ruby annotation — no string parsing needed.

In the snapshot, stored as `"5有あ|る"` (tier prefix, `|` separates okurigana).

## JS Code Structure

The JS is split into two parts:
- **Bootstrap** (inline in index.html): the HTML is minimal
  (`<!DOCTYPE html>`, `<meta charset>`, `<script>`). Defines `B`
  (rANS base-93 decoder returning byte array), sets `D` (arithmetic-coded
  data) and `F` (deflate-compressed payload as base-93). Decodes `F` to
  bytes, decompresses via `DecompressionStream`, and `eval()`s the result.
- **Payload** (`src/kanjimap.js`): sets document title, viewport
  meta tag, CSS, and all application code. Edit this file and run
  `build.py` to rebuild. `build.py` minifies it before gzipping —
  see the Python Tools section for details.

No single-letter aliases for browser APIs — gzip handles repetition
natively, making aliases counterproductive. All browser APIs are called
by their full names (e.g. `document.createElement`, `Math.min`).
Function/IIFE locals use `let`; UI IIFE top-level vars are implicit globals.

### `decodeCell` — decoder IIFE
- Base-93 → bytes → bit generator (rANS `B(D)` + `function*`, no BigInt)
- Arithmetic decoder (32-bit precision): `normalize`, `decode` (model), `decodeUniform`
- Decodes KT (delta-encoded codepoints), kana prob table (81 k² deltas), KN (45 values)
- Returns function `cellKana => [entries...]` for per-cell decoding
- All decoder state `let`-scoped inside IIFE

### `makeEntrySpan(kanji, reading, tier, okurigana, isOn)` — renders one entry as a DOM span with ruby annotation

### Table builder (IIFE)
- Iterates 44 rows × 46 cols, calls `decodeCell(rowLabel+colLabel)` for each cell
- Adds CSS classes: `.empty`, `.first-col`
- Group borders: `.group-left`, `.group-top`, `.group-right`, `.group-bottom`
- First entry gets large font (`.large` class)
- All entries wrapped in `.content` div
- Stores decoded entries on `td._entries` for hover card access

### UI (IIFE)
- Reading toggle (漢/訓/音) — filters on/kun entries; `updateReadings()`
- Theme toggle (light/dark)
- Hover card (`hoverCard`/`hoverCell`): tap cell to show all entries in popup, tap outside to dismiss
- Scale/zoom: `applyScale()`, `resetWillChange()`, `scheduleWillChangeReset()`; adaptive font scaling via `--fs` CSS variable (counter-scales below zoom 1.5x, capped by `fsCap`)
- Drag/pan/coast: `startDrag()`, `moveDrag()`, `endDrag()`, `coast()`; velocity `velX`/`velY`, frame `animFrame`
- Hover card repositioning during drag/coast: `schedHover()`
- Cell entry clipping: `clipCellEntries()` hides partially-visible rows, shows `…` indicator
- Mouse, wheel, and touch event listeners
- Random initial scroll position

## Known Constraints

- All data is in a single arithmetic-coded stream D with multiple hardcoded
  probability models (999-scale) plus 1 stream-decoded kana model
  (82 symbols in codepoint order, unused get minimal probability)
- KN (45 kana for grid layout) also stream-decoded, no ASCII mapping
- H = 0x3042 (あ) used as kana base offset
- No decoder re-initialization between sections
- KT table size (KL) derived from snapshot; no raw encoding path
- 32-bit arithmetic precision; step-based symbol lookup required for
  exact encoder/decoder agreement
- `U(n)` decodes uniform symbols using actual ranges rather than
  rounding up to powers of 2
- All decoder state is `let`-scoped inside the DC IIFE
- Base-93 decoding uses rANS streaming (no BigInt, no block boundaries)

## Python Tools

Scripts for maintaining the kanji reading data. The authoritative data
lives in `src/snapshot.json`; all other files are derived from it.

### Data sources

- `data/kanjidic2.xml` — KANJIDIC2 dictionary: sole source of kanji readings
- `data/JMdict_e.xml` — JMdict dictionary: used only for frequency scoring

Both are gitignored. Download to `data/` from:
- https://www.edrdg.org/wiki/index.php/KANJIDIC_Project
- https://www.edrdg.org/wiki/index.php/JMdict-EDICT_Dictionary_Project

### snapshot.json
Authoritative reference for all cell data. Keyed by cell position
(`row_kana+col_kana`), each value is a list of entry strings in display
order. Format: `"<tier><kanji><reading>[|<okurigana>]"`.

Originally derived from commit aa96857, with subsequent fixes to tiers,
reading choices, sort order, and archaic variant removal.

### resort_by_reading.py
Core scoring library. Parses KANJIDIC2 and JMdict to compute frequency
scores for (kanji, reading) pairs.

Key design decisions:
- **Max-per-word scoring**: each (kanji, reading) pair gets the score of
  its single highest-scoring JMdict word.
- **Primary keb only**: only the first kanji form (keb) in each JMdict
  entry is scored. Alternate spellings (e.g. 噺 as variant of 話) do not
  inherit the primary form's score.
- **Priority tag scoring**: JMdict `ichi1/2`, `news1/2`, `spec1/2`,
  `gai1/2`, and `nf01-48` tags are summed per word. Words with no tags
  get 0.5 (attested but no frequency data).

### expand_entries.py
Expands the dataset with new entries from KANJIDIC2/JMdict and assigns
tiers. Tier thresholds (in `TIER_THRESHOLDS`) are calibrated to
concentrate entries in the middle tiers (j2/j3 ~54%).

Also handles archaic variant removal: traditional kanji forms (e.g. 國)
are removed when their standard Joyo form (国) exists in the same cell.

### rebuild_snapshot.py
Full rebuild pipeline for snapshot.json. Runs four phases:
0. **Remove non-KANJIDIC2 readings**: KANJIDIC2 is the sole source of
   kanji readings. Entries with readings not in KANJIDIC2 are removed.
1. **Fix reading choices**: for each kanji in each cell, pick the
   best KANJIDIC2 reading. Suffixes (dash-prefixed) are deprioritized.
   Among non-suffix forms, highest score wins; okurigana forms win
   ties.
2. **Reassign tiers** based on JMdict frequency scoring.
3. **Re-sort** entries within each cell by score descending.

Usage: `PYTHONPATH=tools python3 tools/rebuild_snapshot.py`

### src/kanjimap.js
The human-readable JS+CSS source. Uses full descriptive variable names
and proper indentation. Contains symbolic placeholders for probability
model values (e.g. `decode(CP)`, `decode(K1)`, `KP[prevTier - 1]`)
that `build.py` replaces with computed literals at build time. Edit
this file for JS/UI changes, then run `build.py` to rebuild.

### kanjimap_processed.js
The minified output produced by `build.py`: placeholders replaced with
computed literals, identifiers renamed to 1- or 2-character symbols,
and all whitespace/comments stripped. For reference only — not used
directly by the build. Regenerated by `build.py`.

### build.py
Builds index.html from `src/kanjimap.js` and `src/snapshot.json`:
1. Computes probability models from `snapshot.json`
2. Replaces symbolic placeholders in JS with computed literals
3. Validates no placeholders remain unreplaced (exits with error if any do)
4. Minifies JS (single-pass tokenizer preserves string literals):
   - Renames identifiers to short symbols (global frequency-based)
   - Scope-aware `let`/`const` renaming: reuses short names (i,j,k...)
     across sibling `{}` scopes for better gzip deduplication
   - Strips comments and whitespace
   - Merges consecutive `let`/`const` declarations
   - Replaces `true`/`false` with `!0`/`!1`
   - Drops redundant semicolons before `}`
5. Encodes snapshot data into arithmetic-coded D string (via `encode_snapshot()`)
6. Deflate-raw compresses the minified JS payload, encodes as base-93 (F string)
7. Assembles final HTML with bootstrap

The identifier rename map is computed dynamically by `compute_rename_map()`:
tokenizes the JS (skipping string literals and property accesses after `.`,
correctly handling `...` spread/rest as non-property-access), counts
standalone identifier frequencies, then assigns 1-char names to the most
frequent and 2-char names to the rest. Browser API names and JS keywords
are excluded via `_EXCLUDED`. No manual maintenance required — new identifiers
are picked up automatically on each build.

Scope-aware renaming (`_build_scope_renames()`) tracks `{}` scopes and
`let`/`const` declarations within each. `for(let ...)` variables are
correctly scoped to the for-body. Short names are assigned per scope and
reused across sibling scopes, avoiding conflicts with arrow/function
parameters and ancestor scope bindings.

Usage: `PYTHONPATH=tools python3 tools/build.py`

### reencode_bac.py
Encodes snapshot.json into the D string using binary arithmetic coding
with probability models. Provides `encode_snapshot()` used by `build.py`,
and can also be run standalone.

The encoder uses 32-bit precision with multiple data-dependent probability
models (see table above) plus 1 stream-decoded kana model and 1 fixed
model (KD_CASE). Includes a built-in `ArithDecoder` that verifies the
round-trip before outputting.

### reencode_da.py
Base-93 codec library using rANS streaming. Provides `encode_b93(bytes)`
/ `decode_b93(str, num_bytes)` for byte↔base-93 conversion, plus
`digit_to_char`/`char_to_digit` helpers. Used by reencode_bac.py,
verify_data.py, and build.py.

### verify_data.py
Decodes the D string from index.html using a Python arithmetic decoder
(32-bit, 999-scale probability tables, `U(k)` uniform decoding) and
compares every entry against src/snapshot.json. Run after any data or
encoding change.

Usage: `python3 tools/verify_data.py`

### Full rebuild procedure

```bash
# 1. Rebuild snapshot (fixes readings, tiers, sort order)
PYTHONPATH=tools python3 tools/rebuild_snapshot.py

# 2. Rebuild index.html (re-encodes D string + re-compresses JS payload)
PYTHONPATH=tools python3 tools/build.py

# 3. Verify
PYTHONPATH=tools python3 tools/verify_data.py
```

Note: do NOT use `.strip()` on the D string — space (U+0020) is a
valid base-93 digit and may appear at the start or end.
