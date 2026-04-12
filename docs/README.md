# Kanjimap

A single-file HTML page (`index.html`) that displays a 44×46 grid of
Japanese kanji organized by reading (pronunciation). All data is
serialized inline as encoded strings. The page is ~30KB and has no
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
Base-93 is decoded to a bit string using BigInt: each group of 13
chars is converted via multiply-accumulate, then `.toString(2).padStart(85,0)`
extracts 85 bits. Char-to-digit: `(charCode+26)*58/59-57|0`

**Arithmetic decoder** (24-bit precision, inside DC IIFE on line 14):
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

**Section 1: KT (Kanji Table)** — 2,698 kanji as delta-encoded codepoints
- 2,697 deltas, each: case selector `Z(535,927,997)` + uniform value
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
      - If 0: `U(2698)` → index into KT table (all kanji are in KT)
      - If 1: end of kanji list. If list empty → end of cell (return).
   b. **on_kun**: `Z(OK)` → 0=kun-yomi, 1=on-yomi
   c. **tier**: first group in cell: `Z(TI)+1` (absolute, tier 1–5);
      subsequent groups: `prev_tier - Z(TD)` (delta-coded, 0–4)
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
| TI (first tier) | [77, 201, 558, 780] | [0, 77, 201, 558, 780, 999] | tier 1–5 (absolute) |
| TD (tier delta) | [637, 931, 990, 998] | [0, 637, 931, 990, 998, 999] | delta 0–4 (prev−curr) |
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

5 tiers assigned by JMdict word frequency (max-per-word scoring).
Archaic/rarely-used kanji forms (JMdict oK/rK/uk/arch/obs tags) are
excluded entirely.
- Tier 5 (j5): score ≥ 98 (~9%) — core readings
- Tier 4 (j4): score ≥ 93 (~16%) — very common
- Tier 3 (j3): score ≥ 49 (~33%) — common
- Tier 2 (j2): score ≥ 5 (~21%) — moderate
- Tier 1 (j1): score < 5 (~21%) — attested, low frequency

Within each cell, tiers are non-increasing (sorted by score descending).
First group encoded absolute: `Z(TI)+1`. Subsequent groups delta-coded:
`prev_tier - Z(TD)`. Delta is 64% zero (same tier), 29% one, so the
delta model compresses much better than flat absolute encoding.

## Entry Format

Each decoded entry is a JS array: `[kanji, reading, tier, okurigana, is_on]`

Example: `['有', 'あ', 5, 'る', 0]` = kanji 有, reading あ, tier 5,
okurigana る, kun-yomi. MP() renders this directly as a DOM span with
ruby annotation — no string parsing needed.

In the snapshot, stored as `"5有あ|る"` (tier prefix, `|` separates okurigana).

## JS Code Structure (index.html)

Naming convention: uppercase 1-letter = global utility aliases,
uppercase 2-letter = project functions/constants, lowercase = variables.
Key mappings: `A`=addEventListener, `l`=classList, `cn`=className setter,
`V`=table element, `S`=scale, `I`=mode index, `Y`=mode array.
Function/IIFE locals use `let`; UI IIFE top-level vars are implicit globals.

### Line 12: DD data string (base-93, arithmetic coded)

### Line 13: Helper functions and aliases
- `A=(o,...a)=>o.addEventListener(...a)` — addEventListener wrapper
- `l=o=>o.classList` — classList accessor
- `cn=(o,c)=>{o.className=c}` — className setter
- `D=document`, `B=D.body`, `$=s=>D.createElement(s)`
- `Q=s=>D.querySelectorAll(s)`, `L`=fromCharCode, `N`=charCodeAt
- `H`=12354 (0x3042)

### Line 14: DC() decoder IIFE
- Base-93 → bit string (BigInt, 13 chars → 85 bits)
- Arithmetic decoder (24-bit precision): `W` (normalize), `Z` (model decode), `U` (uniform decode)
- Bit position `p`, codepoint accumulator `k`
- Decodes KT (2697 deltas), kana prob table (81 deltas), KN (45 values)
- Returns function `s => [entries...]` for cell decoding
- All decoder state `let`-scoped inside IIFE

### Line 15: MP() — renders one entry array as a DOM span with ruby annotation

### Line 16: Table builder (IIFE)
- Iterates 44 rows × 46 cols, calls `DC(rl+cl)` for each cell
- Adds CSS classes: `.e` (empty), `.fc` (first-col)
- Groups borders: `.gl`, `.gt`, `.gr`, `.gb`
- First entry gets large font (`.lg` class)
- All entries wrapped in `.ct` div (overflow:hidden, gradient fade)
- Stores decoded entries on `td._E` for hover card access

### Lines 17–31: UI (IIFE)
- Reading toggle (漢/訓/音) — filters on/kun entries
- Theme toggle (light/dark)
- Hover card (`HC`): tap cell to show all entries in popup, tap outside to dismiss
- Scale functions (`AS`, `RL`, `zr`)
- Minimap (`UM`, `MN`, `SM`); viewport indicator `mw`, CSS `.mm`/`.mv`
- Drag functions (`SD`, `MV`, `ED`); velocity `vx`/`vy`, frame ID `af`
- Update readings: `UR()`
- Mouse, wheel, and touch event listeners
- Random initial scroll position

## Known Constraints

- All data is in a single arithmetic-coded stream DD with 11 hardcoded
  probability models (999-scale) plus 1 stream-decoded kana model
  (82 symbols in codepoint order, unused get minimal probability)
- KN (45 kana for grid layout) also stream-decoded, no ASCII mapping
- H = 0x3042 (あ) used as kana base offset
- No decoder re-initialization between sections
- KT table has 2,698 entries (all kanji); no raw encoding path
- 24-bit arithmetic precision; step-based symbol lookup required for
  exact encoder/decoder agreement
- `U(n)` decodes uniform symbols using actual ranges rather than
  rounding up to powers of 2
- All decoder state is `let`-scoped inside the DC IIFE
- Base-93 decoding uses BigInt `.toString(2)` for 85-bit block conversion

## Python Tools

Scripts for maintaining the kanji reading data. The authoritative data
lives in `snapshot.json`; all other files are derived from it.

### Data sources

- `kanjidic2.xml` — KANJIDIC2 dictionary (kanji readings, grades, frequencies)
- `JMdict_e.xml` — JMdict dictionary (word readings and frequency tags)

Both are gitignored. Download from:
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
Full rebuild pipeline for snapshot.json. Runs three phases:
1. **Fix reading choices**: for each kanji in each cell, pick the
   KANJIDIC2 reading with the highest JMdict score (among readings that
   map to the same cell).
2. **Reassign tiers** based on current thresholds.
3. **Re-sort** entries within each cell by score descending.

Usage: `PYTHONPATH=tools python3 tools/rebuild_snapshot.py`

### reencode_bac.py
Encodes snapshot.json into the DD string using binary arithmetic coding
with probability models. Outputs a base-93 string (2:13 block code).

The encoder uses 24-bit precision and 10 hardcoded + 1 stream-decoded
probability models. The kana model covers all 82 codepoints in order
(no lookup table needed). KN (grid kana mapping) is also stream-encoded.

Includes a built-in `ArithDecoder` that verifies the round-trip before
outputting. The encoder's interval arithmetic must exactly match the JS
decoder's step-based lookup.

Usage: `python3 tools/reencode_bac.py > /tmp/da.txt`

### reencode_da.py
Base-93 codec library. Provides `encode_b93`/`decode_b93` for 2:13
block code conversion (85 bits ↔ 13 chars), plus `digit_to_char`/
`char_to_digit` helpers. Used by reencode_bac.py and verify_data.py.

### verify_data.py
Decodes the DD string from index.html using a Python arithmetic decoder
(24-bit, 999-scale probability tables, `U(k)` uniform decoding) and
compares every entry against snapshot.json. Run after any data or
encoding change.

Usage: `python3 tools/verify_data.py`

### Full rebuild procedure

```bash
# 1. Rebuild snapshot (fixes readings, tiers, sort order)
PYTHONPATH=tools python3 tools/rebuild_snapshot.py

# 2. Re-encode DD string (arithmetic coded)
python3 tools/reencode_bac.py > /tmp/da.txt

# 3. Replace DD in index.html
python3 -c "
import re
with open('index.html') as f: src = f.read()
with open('/tmp/da.txt') as f: da = f.read()
old = re.search(r'DD=\"([^\"]*)\"', src).group(1)
with open('index.html', 'w') as f: f.write(src.replace('DD=\"'+old+'\"', 'DD=\"'+da+'\"'))
"

# 4. Verify
python3 tools/verify_data.py
```

Note: do NOT use `.strip()` on the DD string — space (U+0020) is a
valid base-93 digit and may appear at the start or end.
