# Kanjimap Architecture

## Overview

A single-file HTML page (`index.html`) that displays a 44×46 grid of
Japanese kanji organized by reading (pronunciation). All data is
serialized inline as encoded strings. The page is ~35KB and has no
external dependencies.

## Grid Structure

- **44 rows** × **46 columns** = 2,024 cells
- Rows correspond to the first kana of the reading (あ–わ, 44 of 45 kana)
- Columns: first column is a "header" (readings starting with just that
  kana), remaining 45 columns correspond to the second kana (あ–ん)
- The 45 kana used for grid row/column layout are decoded from the DD
  stream (Section 3) using `U(82)` offsets from `H` (0x3042 = あ)

## Data Encoding

### DD String (Combined Data)

All data is encoded in a single arithmetic-coded stream `DD`, packed
into base-93 via 2:13 block code (13 chars → 85 bits).

**Base-93 alphabet**: U+0020–U+007E excluding `"` and `\` (93 chars).
`G()` decodes base-93 to a bit string using BigInt: each group of 13
chars is converted via multiply-accumulate, then `.toString(2).padStart(85,0)`
extracts 85 bits. Char-to-digit: `(charCode+26)*58/59-57|0`

**Arithmetic decoder** (24-bit precision, inside IIFE on line 13):
- State: `a` (low), `d` (high), `e` (value), all 24-bit
- Constants: `T=1<<23` (TOP), `Q=T/2` (QUARTER), `M=T*2-1` (MASK)
- `W()`: normalization — shifts out resolved bits, reads new bits
- `Z(c)`: decode symbol using cumulative frequency array `c` (999-scale,
  inner values only — implicit 0 and 999). Uses step-based lookup
  matching encoder boundaries.
- `U(n)`: decode uniform symbol 0..n-1. Uses `q=(r)/n|0` for
  single-step range subdivision, matching the encoder.

### DD Stream Layout

The DD stream contains three sections, decoded sequentially from a
single arithmetic-coded bitstream (no re-initialization between sections):

**Section 1: KT (Kanji Table)** — 2,738 kanji as delta-encoded codepoints
- 2,737 deltas, each: case selector `Z(535,927,997)` + uniform value
  - Case 0: `U(4)` + 1 → delta 1–4
  - Case 1: `U(16)` + 5 → delta 5–20
  - Case 2: `U(64)` + 21 → delta 21–84
  - Case 3: `U(512)` + 85 → delta 85–596
- First char is `一` (U+4E00). Each subsequent = previous + delta.

**Section 2: Kana probability table** (82 symbols in codepoint order)
- 81 k² deltas: `U(14)` × 81 — each value k is squared to get the delta,
  building the 999-scale prob table covering all 82 kana offsets (U+3042–U+3093)
- k² approximation saves ~27 bytes vs raw deltas (U(171)) with minimal
  compression loss; the 82nd symbol gets the remainder (999 − sum)
- No explicit kana code list needed; symbol index = codepoint offset

**Section 3: KN (kana row/col mapping)** — 45 kana for grid layout
- First offset: `U(82)` — kana codepoint offset from H (0x3042)
- 44 deltas: `U(4)` × 44 — each delta minus 1 (deltas 1–4, encoded as 0–3)

**Section 4: Cell data** (read per cell, row 0–43, col 0–45)

1. **cell_present**: `Z(CP)` → 0=empty, 1=non-empty
2. If non-empty, loop over kanji groups:
   a. **kanji_type**: `Z(KY)` → 0=kanji, 1=terminator
      - If 0: `U(2738)` → index into KT table (all kanji are in KT)
      - If 1: end of kanji list. If list empty → end of cell (return).
   b. **on_kun**: `Z(OK)` → 0=kun-yomi, 1=on-yomi
   c. **tier_idx**: `Z(TI)+1` → tier 1–6 (natural order, no lookup string)
   d. **variant**: `d1=Z(D1)`, then `d2=Z(D2|d1)-1` (conditional table)
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

| Name | JS Array | Full cumulative | Symbols |
|------|----------|-----------------|---------|
| KD_CASE (delta bucket) | [535, 927, 997] | [0, 535, 927, 997, 999] | 4 / 16 / 64 / 512 |
| CP (cell_present) | [555] | [0, 555, 999] | empty / non-empty |
| KY (kanji_type) | [531] | [0, 531, 999] | kanji / terminator |
| OK (on_kun) | [628] | [0, 628, 999] | kun / on |
| TI (tier_idx) | [163, 335, 526, 811, 932] | [0, 163, 335, 526, 811, 932, 999] | tier 1–6 |
| D1 (d1 offset) | [884] | [0, 884, 999] | 0 / 1 |
| D2\|d1=0 | [71, 886] | [0, 71, 886, 999] | -1 / 0 / 1 |
| D2\|d1=1 | [198, 997] | [0, 198, 997, 999] | -1 / 0 / 1 |
| EF (extra_rd_flag) | [794] | [0, 794, 999] | done / more |
| OF (okuri_flag) | [585] | [0, 585, 999] | done / more |
| KA (kana) | (stream-decoded) | 82-symbol table | kana by codepoint |

### Kana Encoding

All 82 possible kana codepoints (U+3042–U+3093) are covered by a
single probability model `Z(...KA)` where `KA` is an 81-entry
cumulative frequency table decoded from the DD stream. The symbol
index directly equals the kana's codepoint offset from H (0x3042),
so no lookup array is needed. Unused kana get minimal probability (1/999).

The kana codepoint is `Z(...KA) + H + ko` where H=12354 (0x3042)
and ko=96 for on-yomi. The KN string (45 kana for the grid layout)
is also decoded from the DD stream via `U(82)+H`.

### Variant Encoding

Offsets `d1` (first char, 0 or 1) and `d2` (rest, -1/0/1) are applied
to the cell's kana prefix for readings with dakuten/handakuten.
They are encoded as two separate fields with conditional probability:
1. `d1 = Z(884)` — 0 (88.4%) or 1 (11.6%)
2. `d2 = Z(D2|d1) - 1` — uses `Z(71,886)` when d1=0, `Z(198,997)` when d1=1

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

Encoded directly as `Z(TI)+1` where idx 0→tier 1, idx 1→tier 2, etc.
(natural order; no lookup string needed with arithmetic coding).

## Entry Format

Each decoded entry is a JS array: `[kanji, reading, tier, okurigana, is_on]`

Example: `['有', 'あ', 6, 'る', 0]` = kanji 有, reading あ, tier 6,
okurigana る, kun-yomi. K() renders this directly as a DOM span with
ruby annotation — no string parsing needed.

In the snapshot, stored as `"6有あ|る"` (tier prefix, `|` separates okurigana).

## JS Code Structure (index.html)

Most two-letter identifiers have been shortened to single letters for
size. Key mappings: `A`=addEventListener helper, `l`=classList helper,
`V`=table element, `S`=scale, `I`=mode index, `Y`=mode array.

### Line 12: DD data string (base-93, arithmetic coded)

### Line 13: Helper functions and aliases
- `A=(o,...a)=>o.addEventListener(...a)` — addEventListener wrapper
- `l=o=>o.classList` — classList accessor
- `CN=(o,c)=>{o.className=c}` — className setter
- `D=document`, `B=D.body`, `$=s=>D.createElement(s)`
- `Q=s=>D.querySelectorAll(s)`, `L`=fromCharCode, `N`=charCodeAt
- `H`=12354 (0x3042)

### Line 14: DC() decoder IIFE
- Base-93 → bit string (BigInt, 13 chars → 85 bits)
- Arithmetic decoder (24-bit precision): `W` (normalize), `Z` (model decode), `U` (uniform decode)
- Decodes KT (2737 deltas), kana prob table (81 deltas), KN (45 values)
- Returns function `pf => [entries...]` for cell decoding
- All decoder state scoped inside to avoid collisions with outer `D`, `Q`, etc.

### Line 15: K() — renders one entry array as a DOM span with ruby annotation

### Line 16: TM() — toggle expand/collapse of overflow entries

### Line 17: Table builder (IIFE)
- Iterates 44 rows × 46 cols, calls `DC(rl+cl)` for each cell
- Adds CSS classes: `.e` (empty), `.few` (1–2 entries), `.first-col`
- Groups borders: `.gb`, `.gt`, `.gr`, `.gbb`
- Promotes first 1–2 entries to large font (`.lg` class)
- Overflow entries go in `.more` span with `.toggle` button

### Lines 18–32: UI (IIFE)
- Reading toggle (漢/訓/音) — filters on/kun entries
- Theme toggle (light/dark)
- Scale functions (`AS`, `RL`, `Z`)
- Minimap (`UM`, `MN`, `SM`)
- Drag functions (`SD`, `MV`, `ED`)
- Mouse, wheel, and touch event listeners
- Random initial scroll position

## Python Tools

### reencode_bac.py
The BAC encoder. Reads `snapshot.json`, encodes each cell's symbols
using `ArithEncoder` with probability models, converts bits to base-93
via `encode_b93()`, outputs the DD string. Includes built-in
`ArithDecoder` for verification.

**Key**: encoder's `encode_model(cum, sym)` must use the same interval
arithmetic as decoder's `Z()`. The step-based lookup
`while o+(r*c[s+1]/t|0) <= pk` ensures exact round-trip.

### reencode_da.py
Base-93 codec library. Provides `encode_b93`/`decode_b93` for 2:13
block code conversion (85 bits ↔ 13 chars), used by reencode_bac.py
and verify_data.py.

### verify_data.py
Decodes DD from `index.html` using Python arithmetic decoder, compares
against `snapshot.json`. Must match the JS decoder's arithmetic exactly.

### rebuild_snapshot.py
Rebuilds `snapshot.json` from KANJIDIC2/JMdict:
1. Fix reading choices (pick highest-scoring reading per cell)
2. Reassign tiers from frequency scores
3. Re-sort entries by score descending


### resort_by_reading.py / expand_entries.py
Core scoring and data expansion libraries. Used by `rebuild_snapshot.py`.

## Known Constraints

- All data is in a single arithmetic-coded stream DD with 10 hardcoded
  probability models (999-scale) plus 1 stream-decoded kana model
  (82 symbols in codepoint order, 15 unused get minimal probability)
- KN (45 kana for grid layout) also stream-decoded, no ASCII mapping
- H = 0x3042 (あ) used as kana base offset
- No decoder re-initialization between sections
- KT table has 2,738 entries (all kanji); no raw encoding path
- 24-bit arithmetic precision; step-based symbol lookup required for
  exact encoder/decoder agreement
- `U(n)` decodes uniform symbols using actual ranges (e.g. `U(20667)`
  for raw kanji) rather than rounding up to powers of 2
- All decoder state must be inside the IIFE to avoid name collisions
  with outer scope (D=document, Q=querySelectorAll, etc.)
- Base-93 decoding uses BigInt `.toString(2)` for 85-bit block conversion
