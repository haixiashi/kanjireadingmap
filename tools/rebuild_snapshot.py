#!/usr/bin/env python3
"""
Generate data.json from scratch using KANJIDIC2 and JMdict.

This script produces a fully deterministic snapshot with no dependence
on any prior data.json.  The rules:

  Kanji eligibility:
    - Grade 1-9 in KANJIDIC2 (Joyo + Jinmeiyo), OR
    - Grade 10 or no grade, with a KANJIDIC2 newspaper frequency rank.
    - Archaic variants are removed when the standard form is also eligible.

  Reading eligibility:
    - Every ja_on / ja_kun reading listed in KANJIDIC2 (bound-form '-'
      markers stripped).
    - Cell placement uses the KANJIDIC2 okurigana boundary (stem only).
    - JMdict frequency score must be > 0 (the reading appears in JMdict).
    - One entry per kanji per cell; best reading chosen when several
      KANJIDIC2 readings map to the same cell for the same kanji.

  Reading choice (multiple KANJIDIC2 readings -> same cell):
    - Non-bound okurigana form > non-bound bare form > bound form
      (leading or trailing '-' in KANJIDIC2).
    - Within each priority level: highest JMdict max-per-word score wins.

  Sort order (deterministic, no dependence on input order):
    - Kun-yomi before on-yomi (codec constraint).
    - Score weighted by JMdict leading ratio (fraction of words where the
      kanji leads), so suffix-like readings are naturally demoted.
    - Tiebreaker: kd_freq_rank (lower = more common), then codepoint.
    - On-yomi grouped by reading; groups ordered by
      (-best_score, best_kd_freq_rank, reading_text).

Usage: PYTHONPATH=tools python3 tools/rebuild_snapshot.py
"""

import json
import os
import xml.etree.ElementTree as ET
from resort_by_reading import (
    parse_kanjidic2, parse_jmdict, get_reading_freq, parse_entry,
    sort_entries, kata_to_hira, normalize_kanjidic_reading,
    is_katakana, parse_kanjidic2_freq, parse_kanjidic2_grade,
    reading_to_cell, KANJIDIC2_PATH,
)

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(TOOLS_DIR)
SRC_DIR = os.path.join(ROOT_DIR, 'src')
SNAPSHOT_PATH = os.path.join(SRC_DIR, 'data.json')


def parse_kanjidic2_full(path):
    """Parse KANJIDIC2 and return per-kanji info + archaic variant map."""
    tree = ET.parse(path)
    root = tree.getroot()

    kanji_info = {}
    jis_to_kanji = {}
    for char in root.iter('character'):
        lit = char.find('literal').text
        misc = char.find('misc')
        grade_el = misc.find('grade')
        grade = int(grade_el.text) if grade_el is not None else None
        freq_el = misc.find('freq')
        freq = int(freq_el.text) if freq_el is not None else None
        readings = []
        rmg = char.find('reading_meaning')
        if rmg is not None:
            for group in rmg.findall('rmgroup'):
                for r in group.findall('reading'):
                    r_type = r.get('r_type')
                    if r_type in ('ja_on', 'ja_kun'):
                        readings.append((r.text, r_type))
        jis_variants = []
        for var in char.findall('.//variant'):
            if var.get('var_type') == 'jis208':
                jis_variants.append(var.text)
        for cp in char.findall('.//cp_value'):
            if cp.get('cp_type') == 'jis208':
                jis_to_kanji[cp.text] = lit
        if readings:
            kanji_info[lit] = {
                'grade': grade, 'freq': freq,
                'readings': readings, 'jis_variants': jis_variants,
            }

    # Build archaic -> common mapping
    archaic_to_common = {}
    # Method 1: JIS208 variant references
    for lit, info in kanji_info.items():
        for code in info['jis_variants']:
            target = jis_to_kanji.get(code)
            if target and target != lit:
                f_lit = info['freq']
                f_target = kanji_info.get(target, {}).get('freq')
                g_lit = info['grade'] or 99
                g_target = kanji_info.get(target, {}).get('grade') or 99
                if f_lit is not None and f_target is None:
                    archaic_to_common[target] = lit
                elif f_target is not None and f_lit is None:
                    archaic_to_common[lit] = target
                elif f_lit is not None and f_target is not None:
                    # Only apply grade-based archaic when one is grade 10
                    # (Jinmeiyō variant), e.g. 峯(10) → 峰(8).
                    if g_lit == 10 and g_target < 10:
                        archaic_to_common[lit] = target
                    elif g_target == 10 and g_lit < 10:
                        archaic_to_common[target] = lit
    # Method 2: subset reading match
    reading_sets = {}
    for lit, info in kanji_info.items():
        norms = frozenset(
            normalize_kanjidic_reading(r) for r, _ in info['readings']
        )
        if len(norms) >= 2:
            reading_sets[lit] = norms
    for lit, norms in reading_sets.items():
        if lit in archaic_to_common or kanji_info[lit]['freq'] is not None:
            continue
        for other, other_norms in reading_sets.items():
            if other == lit or kanji_info[other]['freq'] is None:
                continue
            if norms <= other_norms:
                archaic_to_common[lit] = other
                break

    return kanji_info, archaic_to_common


def make_entry(kanji, raw_reading, r_type):
    """Create entry string from a KANJIDIC2 reading."""
    clean = raw_reading.strip('-')
    if not clean:
        return None, None
    if r_type == 'ja_on':
        full_hira = kata_to_hira(clean)
        return f"{kanji}{clean}", full_hira
    elif r_type == 'ja_kun':
        if '.' in clean:
            stem, okurigana = clean.split('.', 1)
            full_hira = kata_to_hira(stem + okurigana)
            return f"{kanji}{stem}|{okurigana}", full_hira
        else:
            full_hira = kata_to_hira(clean)
            return f"{kanji}{clean}", full_hira
    return None, None


MIN_EXACT_DOMINANCE_SCORE = 20


def _pick_best_candidate(candidates):
    """Choose the best reading when multiple map to the same cell.

    When on- and kun-yomi compete and the on-yomi has a dominant exact
    JMdict score (>= MIN_EXACT_DOMINANCE_SCORE and strictly best), prefer
    the on-yomi.  Otherwise fall back to the standard priority sort.
    """
    has_on = any(c['r_type'] == 'ja_on' for c in candidates)
    has_kun = any(c['r_type'] == 'ja_kun' for c in candidates)
    if has_on and has_kun:
        best_exact = max(
            candidates,
            key=lambda c: (c['exact_score'],
                           1 if c['r_type'] == 'ja_on' else 0,
                           c['entry']),
        )
        if best_exact['exact_score'] >= MIN_EXACT_DOMINANCE_SCORE:
            runner_up = max(
                (c['exact_score'] for c in candidates
                 if c['entry'] != best_exact['entry']),
                default=0,
            )
            if best_exact['exact_score'] > runner_up:
                return best_exact

    candidates.sort(
        key=lambda c: (
            1 if c['priority'] == 2 else 0,
            -c['score'],
            c['priority'],
        )
    )
    return candidates[0]


def main():
    print("Loading data sources...")
    kanji_info, archaic_to_common = parse_kanjidic2_full(KANJIDIC2_PATH)
    kanji_readings = parse_kanjidic2(KANJIDIC2_PATH)
    freq_map = parse_jmdict(
        os.path.join(ROOT_DIR, 'data', 'JMdict_e.xml'), kanji_readings)
    kd_freq = parse_kanjidic2_freq(KANJIDIC2_PATH)
    kd_grade = parse_kanjidic2_grade(KANJIDIC2_PATH)

    # --- Phase 1: Determine eligible kanji ---
    # Require kd_freq (newspaper frequency rank) OR at least one reading
    # with JMdict frequency tags (score >= 10).  This excludes grade-only
    # kanji with no real-world frequency evidence (e.g. 匁).
    eligible = set()
    for k, info in kanji_info.items():
        g = info['grade']
        if g is None and info['freq'] is None:
            continue
        if info['freq'] is not None:
            eligible.add(k)
            continue
        # Grade-only: check if any reading has JMdict frequency tags.
        best = 0
        for raw, rtype in info['readings']:
            clean = raw.strip('-').replace('.', '')
            hira = kata_to_hira(clean)
            best = max(best, freq_map.get((k, hira), 0))
        if best >= 10:
            eligible.add(k)
    print(f"Phase 1: {len(eligible)} kanji eligible")

    # --- Phase 2: Archaic variant dedup ---
    deduped = set()
    for k in list(eligible):
        common = archaic_to_common.get(k)
        if common and common in eligible:
            deduped.add(k)
            eligible.discard(k)
    print(f"Phase 2: Removed {len(deduped)} archaic variants, "
          f"{len(eligible)} kanji remain")

    # --- Phase 3: Generate entries ---
    # For each eligible kanji, collect all KANJIDIC2 readings that map to
    # valid cells.  When multiple readings map to the same cell, pick the
    # best one using (priority, -score, kd_freq, codepoint).
    snap = {}  # cell_key -> list of entry strings
    entries_total = 0

    for kanji in sorted(eligible):
        info = kanji_info[kanji]

        # Group candidates by cell
        cell_candidates = {}  # cell_key -> list of candidate dicts
        for raw_reading, r_type in info['readings']:
            clean = raw_reading.strip('-')
            if not clean:
                continue
            if r_type == 'ja_on':
                stem_hira = kata_to_hira(clean)
            elif r_type == 'ja_kun':
                stem = clean.split('.')[0] if '.' in clean else clean
                stem_hira = kata_to_hira(stem)
            else:
                continue

            cell = reading_to_cell(stem_hira)
            if cell is None:
                continue
            cell_key = cell[0] + '+' + cell[1]

            entry_str, full_hira = make_entry(kanji, raw_reading, r_type)
            if entry_str is None:
                continue

            # On-yomi codec constraint: first-column allows no extra kana,
            # two-kana columns allow at most one extra kana.  Readings that
            # exceed this are foreign-unit ateji (e.g. メエトル, シリング).
            _, col = cell
            reading_text = entry_str[1:].split('|')[0]
            if is_katakana(reading_text[0]):
                max_len = 1 if col == '' else 3
                if len(reading_text) > max_len:
                    continue

            score = get_reading_freq(kanji, full_hira, freq_map)
            min_score = 0.5 if info['freq'] is not None else 10
            if score < min_score and cell[1] != '':
                continue

            is_bound = raw_reading.startswith('-') or raw_reading.endswith('-')
            has_okuri = '.' in raw_reading
            if is_bound:
                priority = 2
            elif has_okuri:
                priority = 0
            else:
                priority = 1

            exact_score = freq_map.get((kanji, full_hira), 0)
            cell_candidates.setdefault(cell_key, []).append({
                'entry': entry_str,
                'priority': priority,
                'score': score,
                'exact_score': exact_score,
                'r_type': r_type,
            })

        # Pick the best candidate per cell
        for cell_key, candidates in cell_candidates.items():
            best = _pick_best_candidate(candidates)['entry']
            snap.setdefault(cell_key, []).append(best)
            entries_total += 1

    print(f"Phase 3: {entries_total} entries in {len(snap)} cells")

    # --- Phase 4: Drop secondary alternative forms ---
    # When JMdict lists two single-kanji spellings as alternatives of the
    # same word (e.g. 国/邦 for くに), and the secondary kanji's newspaper
    # frequency rank is > 3x worse, drop the secondary.
    alt_dropped = 0
    for cell, entries in snap.items():
        if len(entries) <= 1:
            continue
        by_reading = {}
        for e in entries:
            kanji, reading, okurigana, full_reading = parse_entry(e)
            full_hira = kata_to_hira(full_reading)
            by_reading.setdefault(full_hira, []).append((e, kanji))
        keep = []
        for full_hira, group in by_reading.items():
            if len(group) <= 1:
                keep.extend(e for e, _ in group)
                continue
            # alt_forms is a list of frozenset pairs per reading.
            # Two kanji are alternatives only if they appear in the
            # SAME JMdict entry (same frozenset), not transitively.
            alt_entries = freq_map.alt_forms.get(full_hira, [])
            dropped = set()
            kanji_in_group = {k for _, k in group}
            for primary, secondaries in alt_entries:
                if primary not in kanji_in_group:
                    continue
                primary_fq = kd_freq.get(primary, 99999)
                for k in secondaries & kanji_in_group:
                    ki = kanji_info.get(k, {})
                    g = ki.get('grade')
                    if g is not None and g <= 6:
                        continue
                    fq = kd_freq.get(k, 99999)
                    if fq > primary_fq * 2:
                        dropped.add(k)
                        alt_dropped += 1
            for e, k in group:
                if k not in dropped:
                    keep.append(e)
        snap[cell] = keep
    print(f"Phase 4: Dropped {alt_dropped} secondary alternative forms")

    # --- Phase 5: Sort entries within each cell ---
    for cell, entries in snap.items():
        if len(entries) > 1:
            snap[cell] = sort_entries(entries, freq_map, kd_freq, kd_grade)

    # --- Phase 6: Order cells ---
    # Cells are ordered by (row_kana, col_kana) using the grid kana order.
    kana_order = ("あいうえおかきくけこさしすせそ"
                  "たちつてとなにぬねのはひふへほ"
                  "まみむめもやゆよらりるれろわ")
    col_order = kana_order + "ん"

    def cell_sort_key(cell_key):
        row, col = cell_key.split('+', 1)
        ri = kana_order.index(row) if row in kana_order else 99
        ci = col_order.index(col) if col in col_order else -1
        return (ri, ci)

    ordered = dict(sorted(snap.items(), key=lambda kv: cell_sort_key(kv[0])))

    # --- Write ---
    with open(SNAPSHOT_PATH, 'w') as f:
        json.dump(ordered, f, ensure_ascii=False, indent=1, sort_keys=False)
    print(f"Wrote {SNAPSHOT_PATH}")

    # Summary
    all_kanji = set()
    for entries in ordered.values():
        for e in entries:
            all_kanji.add(e[0])
    total = sum(len(v) for v in ordered.values())
    print(f"\nSummary: {total} entries, {len(all_kanji)} kanji, "
          f"{len(ordered)} cells")

    # Examples
    print("\nExamples:")
    for cell in ['は+な', 'あ+い', 'た+ま', 'あ+']:
        if cell in ordered:
            print(f"  {cell}: {ordered[cell][:5]}")


if __name__ == '__main__':
    main()
