# Kanjimap Architecture

## Overview

A single-file HTML page (`index.html`) that displays a 44×46 grid of
Japanese kanji organized by reading (pronunciation). All data is
serialized inline as encoded strings. The page is ~36KB and has no
external dependencies.

## Grid Structure

- **44 rows** × **46 columns** = 2,024 cells
- Rows correspond to the first kana of the reading (あ–わ, 44 of 45 kana)
- Columns: first column is a "header" (readings starting with just that
  kana), remaining 45 columns correspond to the second kana (あ–ん)
- The kana mapping uses an ASCII encoding string `KN` where each ASCII
  char maps to a kana via `charCode + 12318 (H constant)`
- `KN = "$&(*,-/13579;=?ACFHJLMNOPQTWZ]\`abcdfhjklmnoqu"` (45 chars)

## Data Encoding

### KD String (Kanji Dictionary / KT table)

The KD string encodes a sorted list of 2,048 most-frequent kanji using
delta-encoded codepoints with **arithmetic coding**, packed into base-93
via 2:13 block code (13 chars → 85 bits).

**Encoding**: Each delta is encoded as a case selector (4 symbols,
arithmetic coded via `Z(459,877,993)`) followed by a uniform value:
- Case 0: `U(2)` + 1 → delta 1–4
- Case 1: `U(4)` + 5 → delta 5–20
- Case 2: `U(6)` + 21 → delta 21–84
- Case 3: `U(9)` + 85 → delta 85–596

**Decoding** (JS, inside IIFE on line 13):
KD is decoded first using the arithmetic decoder (Z/U), building the
KT string. Then the decoder is re-initialized with DA for cell data.

`G()` decodes base-93 to a bit string using BigInt: each group of 13
chars is converted to a BigInt via multiply-accumulate, then
`.toString(2).padStart(85,0)` extracts 85 bits.

The first char is `一` (U+4E00). Each subsequent char is previous + delta.

### DA String (Data / cell contents)

The DA string encodes all cell data using **binary arithmetic coding**
with probability models, packed into base-93 via 2:13 block code.

**Base-93 alphabet**: U+0020–U+007E excluding `"` and `\` (93 chars).
Char-to-digit mapping: `G = c => (charCode+26)*58/59-57|0`

**Arithmetic decoder** (24-bit precision, inside IIFE on line 13):
- State: `a` (low), `d` (high), `e` (value), all 24-bit
- Constants: `T=1<<23` (TOP), `Q=T/2` (QUARTER), `M=T*2-1` (MASK)
- `W()`: normalization — shifts out resolved bits, reads new bits
- `Z(c)`: decode symbol using cumulative frequency array `c` (999-scale,
  inner values only — implicit 0 and 999). Uses step-based lookup
  matching encoder boundaries.
- `U(k)`: decode uniform symbol 0..2^k-1. Uses single-step division
  `q=r>>k` (equivalent to `q=r/(1<<k)|0`), matching the encoder.

### Symbol Encoding Order (per cell)

For each cell (row 0–43, col 0–45):

1. **cell_present**: `Z(CP)` → 0=empty, 1=non-empty
2. If non-empty, loop over kanji groups:
   a. **kanji_type**: `Z(KY)` → 0=KT lookup, 1=raw codepoint, 2=terminator
      - If 0: `U(11)` → index into KT table (2048 = 2^11)
      - If 1: `U(15)` → codepoint offset from U+4E00 (32768 = 2^15)
      - If 2: end of kanji list. If list empty → end of cell (return).
   b. **on_kun**: `Z(OK)` → 0=kun-yomi, 1=on-yomi
   c. **tier_idx**: `Z(TI)` → index 0–5, mapped via `'345216'[idx]` to tier digit
   d. **variant**: `d1=Z(D1)`, then `d2=Z(D2|d1)-1` (conditional table)
   e. **Furigana prefix**: reconstructed from cell position + on/kun + variant
   f. **Extra reading**: loop `Z(EF)` → 0=done, 1=more char
      - **kana_type**: `Z(KF)` → 0=K4 (top 4), 1=K6 (next 16), 2=raw
      - Value: `Z(K4M)`, `U(4)`, or `U(7)` respectively
      - Kana code = value + H (+ ko for on-yomi)
   g. **Okurigana** (kun-yomi only): loop `Z(OF)` → 0=done, 1=more char
      - Same kana_type + value decoding as extra reading, but code + H only (no ko)
   h. Assemble entry: `kanji + prefix + extra_reading + tier_char + okurigana`

### Probability Models (999-scale cumulative frequency arrays)

All models use total=999. The JS code inlines the inner boundaries
(implicit 0 at start and 999 at end) directly at each call site
as variadic arguments, e.g. `Z(555)`. The `Z` function uses rest
parameters (`Z=(...c)=>`) to collect them into an array.

| Name | JS Array | Full cumulative | Symbols |
|------|----------|-----------------|---------|
| KD_CASE (delta bucket) | [459, 877, 993] | [0, 459, 877, 993, 999] | 2b / 4b / 6b / 9b |
| CP (cell_present) | [555] | [0, 555, 999] | empty / non-empty |
| KY (kanji_type) | [472, 531] | [0, 472, 531, 999] | kt / raw / term |
| OK (on_kun) | [628] | [0, 628, 999] | kun / on |
| TI (tier_idx) | [191, 477, 597, 769, 932] | [0, 191, 477, 597, 769, 932, 999] | tiers 0–5 |
| D1 (d1 offset) | [885] | [0, 885, 999] | 0 / 1 |
| D2\|d1=0 | [71, 886] | [0, 71, 886, 999] | -1 / 0 / 1 |
| D2\|d1=1 | [199, 998] | [0, 199, 998, 999] | -1 / 0 / 1 |
| EF (extra_rd_flag) | [794] | [0, 794, 999] | done / more |
| KF (kana_type) | [420, 786] | [0, 420, 786, 999] | K4 / K6 / raw |
| OF (okuri_flag) | [585] | [0, 585, 999] | done / more |
| K4M (k4_index) | [452, 685, 859] | [0, 452, 685, 859, 999] | る / う / い / く |

### Kana Encoding

Two lookup strings for frequent kana codes:
- `K4 = "m(&1"` — top 4 kana (る, う, い, く)
- `K6 = ";b9c*-knl3\`LFqJ."` — next 16 kana

The kana code is `charCode - H - ko` where H=12318 and ko=96 for on-yomi.

### Variant Encoding

Offsets `d1` (first char, 0 or 1) and `d2` (rest, -1/0/1) are applied
to the cell's kana prefix for readings with dakuten/handakuten.
They are encoded as two separate fields with conditional probability:
1. `d1 = Z(885)` — 0 (88.5%) or 1 (11.5%)
2. `d2 = Z(D2|d1) - 1` — uses `Z(71,886)` when d1=0, `Z(199,998)` when d1=1

The conditional tables capture the correlation (d1=1, d2=1 is nearly
impossible) without wasting bits on a joint 6-symbol table.

### Tier System

6 tiers assigned by JMdict word frequency (max-per-word scoring):
- Tier 6 (j6): score ≥ 98 (~8%) — core readings
- Tier 5 (j5): score ≥ 93 (~14%) — very common
- Tier 4 (j4): score ≥ 49 (~28%) — common
- Tier 3 (j3): score ≥ 5 (~18%) — moderate
- Tier 2 (j2): score ≥ 0.5 (~17%) — attested, low frequency
- Tier 1 (j1): score = 0 (~15%) — rare / not in JMdict

Stored as index into `'345216'`, so idx 0→tier 3, idx 1→tier 4, etc.
The two most common tiers (3, 4) get the shortest arithmetic codes.

## Entry Format

Each decoded entry is a string: `<kanji><reading><tier_digit><okurigana>`

Example: `有あ6る` = kanji 有, reading あ, tier 6, okurigana る.
In the snapshot, stored as `6有あ|る` (tier prefix, `|` separates okurigana).

## JS Code Structure (index.html)

### Line 12: Globals and data strings
- `R` = String.fromCharCode, `b` = charCodeAt
- `KD` = kanji dictionary string (base-93)
- `DA` = cell data string (base-93, arithmetic coded)

### Line 13: Decoders
- `G(s)`: base-93 → bit string. Uses BigInt: each 13-char block is
  converted via multiply-accumulate (`v=v*93n+BigInt(digit)`), then
  `v.toString(2).padStart(85,0)` extracts 85 bits. Char-to-digit:
  `(CA(s[i])+26)*58/59-57|0`
- `DC()`: **single IIFE** containing all decoding:
  1. Initialize arithmetic decoder with `G(KD)`, decode KT (2047 deltas)
  2. Re-initialize arithmetic decoder with `G(DA)`
  3. Return function `pf => [entries...]` for cell decoding
  - All decoder state (a, d, e, W, Z, U, KT, freq tables) scoped inside
  - `pf` is the KN-encoded cell position string (row+col ASCII chars)

### Line 14: DOM utilities
- `H=12318`, `AE='addEventListener'`, `D=document`, etc.
- `AH()`: converts KN chars to kana for watermarks

### Line 15: K() — renders one entry as a DOM span with ruby annotation

### Line 16: TM() — toggle expand/collapse of overflow entries

### Line 17: Table builder (IIFE)
- Iterates 44 rows × 46 cols, calls `DC(rl+cl)` for each cell
- Adds CSS classes: `.e` (empty), `.few` (1–2 entries), `.first-col`
- Groups borders: `.gb`, `.gt`, `.gr`, `.gbb`
- Promotes first 1–2 entries to large font (`.lg` class)
- Overflow entries go in `.more` span with `.toggle` button

### Lines 18–39: UI (IIFE)
- Reading toggle (漢/訓/音) — filters on/kun entries
- Theme toggle (light/dark)
- Pan/zoom with mouse drag, scroll wheel, touch gestures
- Inertia scrolling
- Minimap with viewport indicator
- Random initial scroll position

## Python Tools

### reencode_bac.py
The BAC encoder. Reads `snapshot.json`, encodes each cell's symbols
using `ArithEncoder` with probability models, converts bits to base-93
via `encode_b93()`, outputs the DA string. Includes built-in
`ArithDecoder` for verification.

**Key**: encoder's `encode_model(cum, sym)` must use the same interval
arithmetic as decoder's `Z()`. The step-based lookup
`while o+(r*c[s+1]/t|0) <= pk` ensures exact round-trip.

### reencode_da.py
The VLC encoder (legacy, still used for KD). Encodes `snapshot.json`
into VLC bitstream, packs into base-93 via `encode_b93()`.

### verify_data.py
Decodes DA from `index.html` using Python arithmetic decoder, compares
against `snapshot.json`. Must match the JS decoder's arithmetic exactly.

### rebuild_snapshot.py
Rebuilds `snapshot.json` from KANJIDIC2/JMdict:
1. Fix reading choices (pick highest-scoring reading per cell)
2. Reassign tiers from frequency scores
3. Re-sort entries by score descending

### transform.js
Legacy one-time migration script (pre-snapshot era). No longer functional
with the current data format. Kept for historical reference only.

### resort_by_reading.py / expand_entries.py
Core scoring and data expansion libraries. Used by `rebuild_snapshot.py`.

## Known Constraints

- Both KD and DA use arithmetic coding with 12 probability models (999-scale)
- KD and DA share the same arithmetic decoder; KD is decoded first, then
  the decoder is re-initialized for DA
- KT table has 2,048 entries; 690 kanji use raw 15-bit encoding
- 24-bit arithmetic precision; step-based symbol lookup required for
  exact encoder/decoder agreement
- `U(k)` decodes uniform symbols using `q=r>>k` (single-step division),
  not repeated halving, to match encoder rounding
- All decoder state must be inside the IIFE to avoid name collisions
  with outer scope (D=document, Q=querySelectorAll, etc.)
- Base-93 decoding uses BigInt `.toString(2)` for 85-bit block conversion
