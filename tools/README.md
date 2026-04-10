# Tools

Scripts for maintaining the kanji reading data. The authoritative data
lives in `snapshot.json`; all other files are derived from it.

See `ARCHITECTURE.md` for detailed documentation of the data encoding,
decoder structure, and probability models.

## Data sources

- `kanjidic2.xml` — KANJIDIC2 dictionary (kanji readings, grades, frequencies)
- `JMdict_e.xml` — JMdict dictionary (word readings and frequency tags)

Both are gitignored. Download from:
- https://www.edrdg.org/wiki/index.php/KANJIDIC_Project
- https://www.edrdg.org/wiki/index.php/JMdict-EDICT_Dictionary_Project

## Files

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
concentrate entries in the middle tiers (j3/j4 ~46%).

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
Encodes snapshot.json into the DA string using binary arithmetic coding
with probability models. Outputs a base-93 string (2:13 block code).

The encoder uses 24-bit precision and 7 probability models for
low-cardinality fields (cell_present, kanji_type, on_kun, tier_idx,
variant, extra_rd_flag, kana_type). High-cardinality fields (kt_idx,
raw_cp, kana values) use uniform encoding.

Includes a built-in `ArithDecoder` that verifies the round-trip before
outputting. The encoder's interval arithmetic must exactly match the JS
decoder's step-based lookup.

Usage: `python3 tools/reencode_bac.py > /tmp/da.txt`

### reencode_da.py
Legacy VLC encoder. Still used for encoding the KD string (kanji
dictionary table). Also provides `encode_b93`/`decode_b93` for base-93
2:13 block code conversion, used by both KD and DA pipelines.

Usage (KD only): `python3 tools/reencode_da.py`

### verify_data.py
Decodes the DA string from index.html using a Python arithmetic decoder
(24-bit, 999-scale probability tables, `U(k)` uniform decoding) and
compares every entry against snapshot.json. Run after any data or
encoding change.

Usage: `python3 tools/verify_data.py`

## Full rebuild procedure

```bash
# 1. Rebuild snapshot (fixes readings, tiers, sort order)
PYTHONPATH=tools python3 tools/rebuild_snapshot.py

# 2. Re-encode DA string (arithmetic coded)
python3 tools/reencode_bac.py > /tmp/da.txt

# 3. Replace DA in index.html
python3 -c "
import re
with open('index.html') as f: src = f.read()
with open('/tmp/da.txt') as f: da = f.read()
old = re.search(r'DA=\"([^\"]*)\"', src).group(1)
with open('index.html', 'w') as f: f.write(src.replace('DA=\"'+old+'\"', 'DA=\"'+da+'\"'))
"

# 4. Verify
python3 tools/verify_data.py
```

Note: do NOT use `.strip()` on the DA string — space (U+0020) is a
valid base-93 digit and may appear at the start or end.
