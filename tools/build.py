#!/usr/bin/env python3
"""Build index.html from kanjimap.js and snapshot.json.

1. Computes probability models from snapshot.json
2. Replaces variable placeholders in kanjimap.js with computed values
3. Encodes snapshot data into arithmetic-coded D string
4. Deflate-raw compresses the JS payload, encodes as base-93 (F string)
5. Assembles final HTML with bootstrap

Usage: PYTHONPATH=tools python3 tools/build.py
"""

import zlib
import os
import re
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(TOOLS_DIR)

sys.path.insert(0, TOOLS_DIR)
from reencode_da import encode_b93

# Identifiers that must never be renamed: JS keywords, browser APIs, bootstrap globals.
_EXCLUDED = {
    # Bootstrap globals
    'B', 'D', 'F',
    # JS keywords
    'let','const','var','if','else','for','while','return','function','new','this',
    'true','false','null','undefined','typeof','instanceof','in','of','break',
    'continue','do','switch','case','default','try','catch','finally','throw',
    'async','await','delete','void','class','import','export',
    # Browser / DOM APIs
    'document','window','Math','String','Array','Object','BigInt','Boolean','Number',
    'parseInt','parseFloat','console','Blob','Response','DecompressionStream',
    'localStorage','performance','requestAnimationFrame','cancelAnimationFrame',
    'setTimeout','clearTimeout','requestIdleCallback',
    'addEventListener','removeEventListener','dispatchEvent',
    'querySelector','querySelectorAll','getElementById','createElement',
    'createTextNode','cloneNode','appendChild','removeChild','insertBefore',
    'classList','style','dataset','innerHTML','textContent','className',
    'offsetWidth','offsetHeight','offsetTop','offsetLeft',
    'scrollWidth','scrollHeight','scrollLeft','scrollTop',
    'clientWidth','clientHeight','clientX','clientY',
    'getBoundingClientRect','getComputedStyle','setProperty','getPropertyValue',
    'getAttribute','setAttribute',
    'parentElement','parentNode','children','childNodes','firstChild','lastChild',
    'contains','matches','closest',
    'preventDefault','stopPropagation','stopImmediatePropagation',
    'touches','changedTouches','targetTouches',
    'deltaY','deltaX','deltaZ','deltaMode',
    'charCodeAt','fromCharCode','toString','padStart','substr','substring','split',
    'replace','indexOf','includes','startsWith','endsWith','trim',
    'push','pop','shift','unshift','splice','slice','map','filter','forEach',
    'find','findIndex','some','every','sort','reverse','join','from','keys','values',
    'min','max','abs','sqrt','hypot','trunc','floor','ceil','round','pow','log2',
    'random','now','assign','keys','entries',
    'width','height','left','top','right','bottom',
    'href','src','alt','title','type','name','value','checked','disabled',
    'target','currentTarget','relatedTarget','detail',
    'tagName','nodeName','nodeType','nodeValue',
    'append','prepend','after','before','remove',
    'blur','focus','click','submit','reset',
}


def compute_rename_map(js_code):
    """Compute identifier rename map dynamically from js_code.

    Tokenizes the JS, counts standalone identifier frequencies (skipping
    property accesses after '.'), then assigns short names by frequency:
    1-char names to the most frequent, 2-char names to the rest.
    """
    import re
    TOKEN_RE = re.compile(
        r'("(?:[^"\\]|\\.)*")'
        r"|('(?:[^'\\]|\\.)*')"
        r'|(`(?:[^`\\]|\\.)*`)'
        r'|(//[^\n]*)'
        r'|(/\*[\s\S]*?\*/)'
        r'|([a-zA-Z_$][a-zA-Z0-9_$]*)'
        r'|(0[xX][0-9a-fA-F]+|[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)'
        r'|(\s+)'
        r'|(.)'
    )

    freq = {}
    prev_dot = False  # was the previous non-whitespace token '.'?

    for m in TOKEN_RE.finditer(js_code):
        str_dq, str_sq, tmpl, lcmt, bcmt, ident, num, ws, other = m.groups()
        if str_dq or str_sq or tmpl or lcmt or bcmt or num:
            prev_dot = False
        elif ident:
            if not prev_dot:
                freq[ident] = freq.get(ident, 0) + 1
            prev_dot = False
        elif ws:
            pass  # don't update prev_dot
        else:
            prev_dot = (other == '.')

    # Candidates: not excluded, length > 2, freq >= 2
    candidates = sorted(
        [(name, count) for name, count in freq.items()
         if name not in _EXCLUDED and len(name) > 2 and count >= 2],
        key=lambda x: (-x[1], x[0])  # sort by freq desc, then name for determinism
    )

    # 1-char pool: all single chars not already used as identifiers in the source
    used_as_ident = {name for name in freq if len(name) == 1}
    pool_1 = [c for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$'
              if c not in used_as_ident]

    # 2-char pool: generated on demand, skipping any already used as identifiers
    def gen_2char(used_targets):
        chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$'
        for c1 in chars:
            for c2 in chars:
                name = c1 + c2
                if name not in freq and name not in used_targets:
                    yield name

    rename_map = {}
    used_targets = set()
    pool_1_idx = 0
    pool_2 = gen_2char(used_targets)

    for name, count in candidates:
        if pool_1_idx < len(pool_1):
            target = pool_1[pool_1_idx]
            pool_1_idx += 1
        else:
            target = next(pool_2)
        rename_map[name] = target
        used_targets.add(target)

    n1 = sum(1 for v in rename_map.values() if len(v) == 1)
    n2 = sum(1 for v in rename_map.values() if len(v) == 2)
    print(f"Rename map: {len(rename_map)} identifiers ({n1} × 1-char, {n2} × 2-char)",
          file=__import__('sys').stderr)
    return rename_map


def minify_js(code, rename_map):
    """Strip comments, rename identifiers, collapse whitespace.

    Uses a single-pass tokenizer so string literals are never modified.
    Whitespace is dropped and re-inserted only where required between
    adjacent word tokens (identifiers/keywords/numbers).
    """
    import re
    TOKEN_RE = re.compile(
        r'("(?:[^"\\]|\\.)*")'                             # double-quoted string
        r"|('(?:[^'\\]|\\.)*')"                            # single-quoted string
        r'|(`(?:[^`\\]|\\.)*`)'                            # template literal
        r'|(//[^\n]*)'                                     # line comment
        r'|(/\*[\s\S]*?\*/)'                               # block comment
        r'|([a-zA-Z_$][a-zA-Z0-9_$]*)'                    # identifier / keyword
        r'|(0[xX][0-9a-fA-F]+|[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)'  # number (hex or decimal)
        r'|(\s+)'                                          # whitespace
        r'|(.)'                                            # any other char
    )
    out = []
    prev_word = False  # last emitted token was an identifier/keyword/number

    for m in TOKEN_RE.finditer(code):
        str_dq, str_sq, template, line_cmt, block_cmt, ident, num, ws, other = m.groups()

        if str_dq or str_sq or template:
            out.append(m.group(0))
            prev_word = False
        elif line_cmt or block_cmt:
            pass  # strip
        elif ident:
            tok = rename_map.get(ident, ident)
            if prev_word:
                out.append(' ')
            out.append(tok)
            prev_word = True
        elif num:
            if prev_word:
                out.append(' ')
            out.append(m.group(0))
            prev_word = True
        elif ws:
            pass  # strip; spacing re-added by prev_word logic
        else:
            out.append(other)
            prev_word = False

    result = ''.join(out)

    # Merge consecutive let/const declarations: let a=1;let b=2 → let a=1,b=2
    for kw in ('let ', 'const '):
        while True:
            merged = False
            i = result.find(kw)
            while i >= 0:
                # Find the semicolon ending this declaration
                # Track nesting depth to skip over function bodies, arrays, objects
                depth = 0
                j = i + len(kw)
                while j < len(result):
                    c = result[j]
                    if c in '({[':
                        depth += 1
                    elif c in ')}]':
                        depth -= 1
                    elif c == ';' and depth == 0:
                        break
                    j += 1
                # Check if immediately followed by same keyword
                if j < len(result) and result[j:j+1+len(kw)] == ';' + kw:
                    result = result[:j] + ',' + result[j+1+len(kw):]
                    merged = True
                    i = result.find(kw, j)  # continue from merge point
                else:
                    i = result.find(kw, j + 1)
            if not merged:
                break

    # Replace true/false with !0/!1
    result = result.replace('true', '!0').replace('false', '!1')

    # Remove semicolons before closing braces (last statement in block)
    result = result.replace(';}', '}')

    return result


def main():
    # Read the JS payload
    with open(os.path.join(TOOLS_DIR, 'kanjimap.js')) as f:
        js_payload = f.read()

    # Compute models from snapshot and inject into JS
    import json as _json
    from reencode_bac import (compute_models, M_CELL, M_KT0, M_KT1, M_ONKUN,
                               M_TDP, M_D1K, M_D1O, M_D2_0, M_D2_1,
                               M_EXTRA, M_OKURI)
    with open(os.path.join(TOOLS_DIR, 'snapshot.json')) as f:
        snap = _json.load(f)
    compute_models(snap)
    from reencode_bac import (M_CELL, M_KT0, M_KT1, M_ONKUN,
                               M_TDP, M_D1K, M_D1O, M_D2_0, M_D2_1,
                               M_EXTRA, M_OKURI)

    # Inline model values into JS (replace variable refs with literals)
    def inner(m):
        return m[1:-1] if isinstance(m[0], int) else m

    # Compute KT length from snapshot
    all_kanji = set()
    for entries in snap.values():
        for e in entries:
            cp = ord(e[1])
            if 0x4E00 <= cp < 0x10000:
                all_kanji.add(e[1])
    kl = len(all_kanji)

    # Encode D string from snapshot (needed for DL placeholder)
    from reencode_bac import encode_snapshot
    dd, d_num_bytes = encode_snapshot(snap)

    # Replace variable placeholders with computed values
    from reencode_bac import M_KD_CASE
    kd = ','.join(str(x) for x in inner(M_KD_CASE))
    kp = ','.join(str(inner(m)[0]) for m in M_KT0)
    tp = ','.join(str(inner(m)) for m in M_TDP[1:])
    d2_0 = ','.join(str(x) for x in inner(M_D2_0))
    d2_1 = ','.join(str(x) for x in inner(M_D2_1))
    replacements = [
        ('decode(KD)', f'decode({kd})'),
        ('decodeUniform(KL)', f'decodeUniform({kl})'),
        ('KL - 1', f'{kl-1}'),
        ('decode(CP)', f'decode({inner(M_CELL)[0]})'),
        ('decode(K1)', f'decode({inner(M_KT1)[0]})'),
        ('decode(OK)', f'decode({inner(M_ONKUN)[0]})'),
        ('decode(isOn ? DO : DK)', f'decode(isOn?{inner(M_D1O)[0]}:{inner(M_D1K)[0]})'),
        ('decode(D0)', f'decode({d2_0})'),
        ('decode(D1)', f'decode({d2_1})'),
        ('decode(EF)', f'decode({inner(M_EXTRA)[0]})'),
        ('decode(OF)', f'decode({inner(M_OKURI)[0]})'),
        ('KP[prevTier - 1]', f'[{kp}][prevTier-1]'),
        ('TP[prevTier - 1]', f'[{tp.replace(" ","")}][prevTier-1]'),
    ]
    for old, new in replacements:
        js_payload = js_payload.replace(old, new)

    # Validate: check no symbolic placeholders remain unreplaced
    import re as _re
    KNOWN_PLACEHOLDERS = ['KD', 'KL', 'CP', 'K1', 'OK', 'DO', 'DK', 'D0', 'D1',
                          'EF', 'OF', 'KP', 'TP']
    for ph in KNOWN_PLACEHOLDERS:
        if _re.search(r'\b' + ph + r'\b', js_payload):
            print(f"ERROR: placeholder {ph!r} was not replaced in JS", file=sys.stderr)
            sys.exit(1)

    # Minify: rename identifiers + strip whitespace/comments
    js_minified = minify_js(js_payload, compute_rename_map(js_payload))
    print(f"JS: {len(js_payload)} bytes → minified: {len(js_minified)} bytes", file=sys.stderr)

    # Write minified JS for reference
    with open(os.path.join(TOOLS_DIR, 'kanjimap_processed.js'), 'w') as f:
        f.write(js_minified)

    # Gzip the minified JS payload
    gz = zlib.compress(js_minified.encode('utf-8'), level=9, wbits=-15)
    print(f"Minified: {len(js_minified)} bytes → gzip: {len(gz)} bytes", file=sys.stderr)

    # Encode gzipped bytes as base-93
    gz_b93 = encode_b93(list(gz))
    print(f"Base-93: {len(gz_b93)} chars", file=sys.stderr)

    bootstrap = (
        'D="' + dd + '";\n'
        'F="' + gz_b93 + '";\n'
        # rANS base-93 byte decoder (no BigInt, sentinel-terminated)
        'B=s=>{let i=0,v=0,o=[];do{'
        'while(v<2**24&&i<s.length)v=v*93+(s.charCodeAt(i++)+26)*58/59-57|0;'
        'o.push(v&255);v>>=8}while(v>1);return o};\n'
        # Decode F from base-93, decompress, eval payload
        '(async()=>{'
        'let a=new Uint8Array(B(F));'
        'let s=new Blob([a]).stream().pipeThrough(new DecompressionStream("deflate-raw"));'
        'eval(await new Response(s).text())'
        '})()'
    )

    # Build the HTML
    out = (
        '<!DOCTYPE html>\n'
        '<meta charset="UTF-8">\n'
        '<script>\n'
        + bootstrap + '\n'
        '</script>\n'
    )

    out_path = os.path.join(ROOT_DIR, 'index.html')
    with open(out_path, 'w') as f:
        f.write(out)

    file_size = os.path.getsize(out_path)
    print(f"Output: {file_size} bytes", file=sys.stderr)



if __name__ == '__main__':
    main()
