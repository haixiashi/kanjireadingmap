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
from reencode_da import encode_b93, decode_b93

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

    # Replace variable placeholders with computed values
    from reencode_bac import M_KD_CASE
    kd = ','.join(str(x) for x in inner(M_KD_CASE))
    kp = ','.join(str(inner(m)[0]) for m in M_KT0)
    tp = ','.join(str(inner(m)) for m in M_TDP[1:])
    d2_0 = ','.join(str(x) for x in inner(M_D2_0))
    d2_1 = ','.join(str(x) for x in inner(M_D2_1))
    replacements = [
        ('decode(KD)', f'decode({kd})'),
        ('decodeU(KL)', f'decodeU({kl})'),
        ('KL-1', f'{kl-1}'),
        ('decode(CP)', f'decode({inner(M_CELL)[0]})'),
        ('decode(K1)', f'decode({inner(M_KT1)[0]})'),
        ('decode(OK)', f'decode({inner(M_ONKUN)[0]})'),
        ('decode(isOn?DO:DK)', f'decode(isOn?{inner(M_D1O)[0]}:{inner(M_D1K)[0]})'),
        ('decode(D0)', f'decode({d2_0})'),
        ('decode(D1)', f'decode({d2_1})'),
        ('decode(EF)', f'decode({inner(M_EXTRA)[0]})'),
        ('decode(OF)', f'decode({inner(M_OKURI)[0]})'),
        ('KP[prevTier-1]', f'[{kp}][prevTier-1]'),
        ('TP[prevTier-1]', f'[{tp.replace(" ","")}][prevTier-1]'),
    ]
    for old, new in replacements:
        js_payload = js_payload.replace(old, new)

    # Write processed JS for reference
    with open(os.path.join(TOOLS_DIR, 'kanjimap_processed.js'), 'w') as f:
        f.write(js_payload)

    # Encode D string from snapshot
    from reencode_bac import encode_snapshot
    dd = encode_snapshot(snap)

    # Gzip the JS payload
    gz = zlib.compress(js_payload.encode('utf-8'), level=9, wbits=-15)
    print(f"JS: {len(js_payload)} bytes → gzip: {len(gz)} bytes", file=sys.stderr)

    # Encode gzipped bytes as base-93
    # Convert bytes to bit string
    bits = []
    for byte in gz:
        for bit in range(7, -1, -1):
            bits.append((byte >> bit) & 1)
    gz_b93 = encode_b93(bits)
    print(f"Base-93: {len(gz_b93)} chars", file=sys.stderr)

    # Bootstrap: decode base-93 → bytes → decompress → eval
    # F contains the base-93 gzipped JS
    # D contains the arithmetic-coded data
    # The bootstrap decodes F, decompresses, and evals the result
    # The eval'd code can access D as a global
    bootstrap = (
        'D="' + dd + '";\n'
        'F="' + gz_b93 + '";\n'
        # Shared base-93 decoder (used by bootstrap and eval'd DC decoder)
        'B=s=>{let b="",v=0n;'
        '[...s].map((c,i)=>{'
        'v=v*93n+BigInt((c.charCodeAt(0)+26)*58/59-57|0);'
        '++i%13||(b+=v.toString(2).padStart(85,0),v=0n)});'
        'return b};\n'
        # Decode F from base-93, truncated to exact gzip length
        '(async()=>{'
        'let b=B(F);'
        'let a=new Uint8Array(' + str(len(gz)) + ');'
        'for(let i=0;i<' + str(len(gz)) + ';i++)'
        'a[i]=parseInt(b.substr(i*8,8),2);'
        # Decompress via DecompressionStream
        'let s=new Blob([a]).stream().pipeThrough(new DecompressionStream("deflate-raw"));'
        'eval(await new Response(s).text())'
        '})()'
    )

    # Build the HTML
    out = (
        '<!DOCTYPE html>\n'
        '<html lang="ja">\n'
        '<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">\n'
        '<title>漢字読み方表</title>\n'
        '</head>\n'
        '<body>\n'
        '<script>\n'
        + bootstrap + '\n'
        '</script>\n'
        '</body>\n'
        '</html>\n'
    )

    out_path = os.path.join(ROOT_DIR, 'index.html')
    with open(out_path, 'w') as f:
        f.write(out)

    file_size = os.path.getsize(out_path)
    print(f"Output: {file_size} bytes", file=sys.stderr)



if __name__ == '__main__':
    main()
