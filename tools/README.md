# Tools

Scripts for maintaining the kanji reading data. The authoritative data
lives in `snapshot.json`; all other files are derived from it.

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
  its single highest-scoring JMdict word. This avoids inflating readings
  that appear in many compounds.
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

### reencode_da.py
Encodes snapshot.json into the binary DA string used in index.html.
The DA string is a base-85 encoding of a variable-length bitstream
representing the 44x46 cell grid.

Usage: `python3 tools/reencode_da.py > /tmp/da.txt`

Then replace the DA="..." string in index.html.

### verify_data.py
Decodes the DA string from index.html and compares every entry against
snapshot.json. Run after any data-encoding change.

Usage: `python3 tools/verify_data.py`

## Full rebuild procedure

```bash
# 1. Rebuild snapshot (fixes readings, tiers, sort order)
PYTHONPATH=tools python3 tools/rebuild_snapshot.py

# 2. Re-encode DA string
python3 tools/reencode_da.py > /tmp/da.txt

# 3. Replace DA in index.html (manual or scripted)
python3 -c "
import re
with open('index.html') as f: src = f.read()
with open('/tmp/da.txt') as f: da = f.read().strip()
old = re.search(r'DA=\"([^\"]*)\"', src).group(1)
with open('index.html', 'w') as f: f.write(src.replace('DA=\"'+old+'\"', 'DA=\"'+da+'\"'))
"

# 4. Verify
python3 tools/verify_data.py
```
