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
delta-encoded codepoints. Stored as base-93 using 2:13 block code
(2 chars → 13 bits).

**Encoding**: Delta VLC with buckets:
- `0` + 2 bits: delta 1–4
- `10` + 4 bits: delta 5–20
- `110` + 6 bits: delta 21–84
- `111` + 9 bits: delta 85–596

**Decoding** (JS, line 13):
```
BD(KD) → bit array → R(n) reads n bits → build KT string
```
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
- `Z(c)`: decode symbol using cumulative frequency array `c`,
  total = `c.at(-1)`. Uses step-based lookup matching encoder boundaries.
- `U(n)`: decode uniform symbol 0..n-1

### Symbol Encoding Order (per cell)

For each cell (row 0–43, col 0–45):

1. **cell_present**: `Z(CP)` → 0=empty, 1=non-empty
2. If non-empty, loop over kanji groups:
   a. **kanji_type**: `Z(KY)` → 0=KT lookup, 1=raw codepoint, 2=terminator
      - If 0: `U(2048)` → index into KT table
      - If 1: `U(32768)` → codepoint offset from U+4E00
      - If 2: end of kanji list. If list empty → end of cell (return).
   b. **on_kun**: `Z(OK)` → 0=kun-yomi, 1=on-yomi
   c. **tier_idx**: `Z(TI)` → index 0–5, mapped via `'345216'[idx]` to tier digit
   d. **variant**: `Z(VR)` → Dv 0–5, encodes d1/d2 kana offsets
   e. **Furigana prefix**: reconstructed from cell position + on/kun + variant
   f. **Extra reading**: loop `Z(EF)` → 0=done, 1=more char
      - **kana_type**: `Z(KF)` → 0=K4 (top 4), 1=K6 (next 16), 2=raw
      - Value: `U(4)`, `U(16)`, or `U(128)` respectively
      - Kana code = value + H (+ ko for on-yomi)
   g. **Okurigana** (kun-yomi only): loop `U(2)` → 0=done, 1=more char
      - Same kana_type + value decoding as extra reading, but code + H only (no ko)
   h. Assemble entry: `kanji + prefix + extra_reading + tier_char + okurigana`

### Probability Models (cumulative frequency arrays)

| Name | Array | Total | Symbols |
|------|-------|-------|---------|
| CP (cell_present) | [0, 1125, 2024] | 2024 | empty / non-empty |
| KY (kanji_type) | [0, 5598, 6288, 11836] | 11836 | kt / raw / term |
| OK (on_kun) | [0, 2923, 4649] | 4649 | kun / on |
| TI (tier_idx) | [0, 889, 2219, 2780, 3580, 4337, 4649] | 4649 | tiers 0–5 |
| VR (variant) | [0, 3357, 3823, 3929, 4356, 4357, 4649] | 4649 | Dv 0–5 |
| EF (extra_rd_flag) | [0, 4649, 5847] | 5847 | done / more |
| KF (kana_type) | [0, 1373, 2566, 3263] | 3263 | K4 / K6 / raw |

Okurigana flags use uniform `U(2)` — no probability model (not worth it).

### Kana Encoding

Two lookup strings for frequent kana codes:
- `K4 = "m(&1"` — top 4 kana (る, う, い, く)
- `K6 = ";b9c*-knl3\`LFqJ."` — next 16 kana

The kana code is `charCode - H - ko` where H=12318 and ko=96 for on-yomi.

### Variant Encoding

The variant `Dv` (0–5) encodes offsets `d1` (first char) and `d2` (rest)
applied to the cell's kana prefix for readings with dakuten/handakuten:
```
d2 = (Dv+1) % 3 - 1     → values: -1, 0, 1
d1 = ((Dv-d2) / 3) % 2   → values: 0, 1
```

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
- `FC` = String.fromCharCode, `CA` = charCodeAt
- `KD` = kanji dictionary string (base-93)
- `DA` = cell data string (base-93, arithmetic coded)

### Line 13: Decoders
- `G()`: char-to-digit for base-93
- `BD()`: base-93 2:13 block decoder → bit array
- KT building: decode KD deltas via `R(n)` bit reader
- `DC()`: **inside IIFE** — arithmetic decoder + cell parser
  - All decoder state (a, d, e, W, Z, U, freq tables) scoped inside
  - Returns a function `pf => [entries...]`
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

### resort_by_reading.py / expand_entries.py
Core scoring and data expansion libraries. Used by `rebuild_snapshot.py`.

## Known Constraints

- KD still uses VLC + base-93 2:13 block code (not arithmetic coded)
- DA uses arithmetic coding with 7 probability models
- Okurigana flags use uniform `U(2)` (too few bits saved for a model)
- KT table has 2,048 entries; 690 kanji use raw 16-bit encoding
- 24-bit arithmetic precision; step-based symbol lookup required for
  exact encoder/decoder agreement
- All decoder state must be inside the IIFE to avoid name collisions
  with outer scope (D=document, Q=querySelectorAll, U=update, etc.)
- `UM()` must be defined before its first call on the same execution path
