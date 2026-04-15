#!/usr/bin/env python3
"""Build index.html from kanjimap.js and data.json.

1. Computes probability models from data.json
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
SRC_DIR = os.path.join(ROOT_DIR, 'src')

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

_TOKEN_RE = re.compile(
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


def _iter_js_tokens(js_code):
    for m in _TOKEN_RE.finditer(js_code):
        str_dq, str_sq, tmpl, lcmt, bcmt, ident, num, ws, other = m.groups()
        if str_dq:
            kind = 'str_dq'
        elif str_sq:
            kind = 'str_sq'
        elif tmpl:
            kind = 'template'
        elif lcmt:
            kind = 'line_comment'
        elif bcmt:
            kind = 'block_comment'
        elif ident:
            kind = 'ident'
        elif num:
            kind = 'num'
        elif ws:
            kind = 'ws'
        else:
            kind = 'other'
        yield kind, m.group(0), m.start()


def _iter_local_targets(used):
    first_chars = 'ijklmnopqrstuvwxyzabcdefghABCDEFGHIJKLMNOPQRSTUVWXYZ_$'
    tail_chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$'
    for name in first_chars:
        if name not in used and name not in _EXCLUDED:
            yield name
    for c1 in first_chars:
        for c2 in tail_chars:
            name = c1 + c2
            if name not in used and name not in _EXCLUDED:
                yield name


def compute_rename_map(js_code):
    """Compute identifier rename map dynamically from js_code.

    Tokenizes the JS, counts standalone identifier frequencies (skipping
    property accesses after '.'), then assigns short names by frequency:
    1-char names to the most frequent, 2-char names to the rest.
    """
    freq = {}
    prev_dot = False  # was the previous non-whitespace token a single '.'?
    dot_count = 0     # consecutive dot count (1=property access, 3=spread)

    for kind, tok, _ in _iter_js_tokens(js_code):
        if kind in ('str_dq', 'str_sq', 'template', 'line_comment', 'block_comment', 'num'):
            prev_dot = False
            dot_count = 0
        elif kind == 'ident':
            if not prev_dot:
                freq[tok] = freq.get(tok, 0) + 1
            prev_dot = False
            dot_count = 0
        elif kind == 'ws':
            pass  # don't update prev_dot or dot_count
        else:
            if tok == '.':
                dot_count += 1
            else:
                dot_count = 0
            prev_dot = (tok == '.' and dot_count == 1)

    # Candidates: not excluded, length > 2, freq >= 2
    candidates = sorted(
        [(name, count) for name, count in freq.items()
         if name not in _EXCLUDED and len(name) > 2 and count >= 2],
        key=lambda x: (-x[1], x[0])  # sort by freq desc, then name for determinism
    )

    # 1-char pool: single chars not already used as identifiers in the source.
    # _ and $ are intentionally excluded: the scope-aware local renamer uses them
    # and can reuse them freely across sibling scopes. If the global rename map
    # claimed _ or $, they would be added to global_targets and blocked from
    # every local scope, costing many bytes whenever the global pool shifts and
    # a different identifier gets _ or $. Keeping them local-only makes the
    # output size stable regardless of how many global identifiers are added.
    used_as_ident = {name for name in freq if len(name) == 1}
    pool_1 = [c for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
              if c not in used_as_ident]

    # 2-char pool: generated on demand, skipping any already used as identifiers
    def gen_2char(used_targets):
        first_chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_$'
        tail_chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$'
        for c1 in first_chars:
            for c2 in tail_chars:
                name = c1 + c2
                if (name not in freq and name not in used_targets
                        and name not in _EXCLUDED):
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


def _build_scope_renames(code, rename_map):
    """Build token-level renames for let/const bindings.

    The analysis models lexical block scopes plus explicit loop-header scopes
    for `for (let ...)` so bindings remain visible in both the header and body.
    Local short names are assigned per scope by binding frequency, with names
    reused across nested scopes unless the child subtree still references the
    outer binding.
    """

    scopes = {
        0: {
            'parent': None,
            'children': [],
            'bindings': [],
            'binding_by_name': {},
        },
    }
    bindings = {}
    token_bindings = {}
    scope_short_direct = {}
    active_scopes = [0]
    next_scope_id = 1
    next_binding_id = 0

    prev_dot = False
    dot_count = 0
    pending_for = False
    decl_ctx = None
    loop_stack = []

    def current_scope():
        return active_scopes[-1]

    def new_scope(parent_id):
        nonlocal next_scope_id
        scope_id = next_scope_id
        next_scope_id += 1
        scopes[scope_id] = {
            'parent': parent_id,
            'children': [],
            'bindings': [],
            'binding_by_name': {},
        }
        scopes[parent_id]['children'].append(scope_id)
        return scope_id

    def declare_binding(name, pos):
        nonlocal next_binding_id
        scope_id = current_scope()
        binding_id = next_binding_id
        next_binding_id += 1
        bindings[binding_id] = {
            'name': name,
            'scope': scope_id,
            'decl_pos': pos,
            'count': 1,
            'use_scopes': set(),
            'positions': [pos],
            'renamable': len(name) > 2 or name in rename_map,
        }
        scopes[scope_id]['bindings'].append(binding_id)
        scopes[scope_id]['binding_by_name'][name] = binding_id
        token_bindings[pos] = binding_id
        return binding_id

    def resolve_binding(name):
        for scope_id in reversed(active_scopes):
            binding_id = scopes[scope_id]['binding_by_name'].get(name)
            if binding_id is not None:
                return binding_id
        return None

    def add_short_ident(name):
        scope_short_direct.setdefault(current_scope(), set()).add(name)

    def decl_top_level():
        return decl_ctx and not any(decl_ctx[k] for k in ('paren', 'brace', 'bracket'))

    for kind, tok, pos in _iter_js_tokens(code):
        if kind == 'ws':
            continue

        if kind in ('line_comment', 'block_comment'):
            continue

        if loop_stack and loop_stack[-1]['phase'] == 'await_body' and kind != 'ws':
            if not (kind == 'other' and tok == '{'):
                loop_stack[-1].update({
                    'phase': 'stmt',
                    'stmt_paren': 0,
                    'stmt_brace': 0,
                    'stmt_bracket': 0,
                })

        if kind in ('str_dq', 'str_sq', 'template', 'num'):
            prev_dot = False
            dot_count = 0
            pending_for = False
            continue

        if kind == 'ident':
            if (decl_ctx and decl_ctx['in_for'] and decl_top_level()
                    and not decl_ctx['expect_name'] and not prev_dot
                    and tok in ('in', 'of')):
                decl_ctx = None
                prev_dot = False
                dot_count = 0
                pending_for = False
                continue

            if decl_ctx and decl_ctx['expect_name'] and not prev_dot:
                binding_id = declare_binding(tok, pos)
                if len(tok) <= 2 and not bindings[binding_id]['renamable']:
                    add_short_ident(tok)
                decl_ctx['expect_name'] = False
            else:
                if not prev_dot:
                    binding_id = resolve_binding(tok)
                    if binding_id is not None:
                        bindings[binding_id]['count'] += 1
                        bindings[binding_id]['use_scopes'].add(current_scope())
                        bindings[binding_id]['positions'].append(pos)
                        token_bindings[pos] = binding_id
                    elif len(tok) <= 2:
                        add_short_ident(tok)

                if not prev_dot and tok == 'for':
                    pending_for = True
                elif not prev_dot and tok in ('let', 'const'):
                    decl_ctx = {
                        'expect_name': True,
                        'in_for': bool(loop_stack and loop_stack[-1]['phase'] == 'header'),
                        'paren': 0,
                        'brace': 0,
                        'bracket': 0,
                    }
                    pending_for = False
                else:
                    pending_for = False

            prev_dot = False
            dot_count = 0
            continue

        if tok == '(':
            if pending_for:
                loop_scope = new_scope(current_scope())
                active_scopes.append(loop_scope)
                loop_stack.append({
                    'scope': loop_scope,
                    'phase': 'header',
                    'paren_depth': 1,
                    'body_block': None,
                })
                pending_for = False
            elif loop_stack and loop_stack[-1]['phase'] == 'header':
                loop_stack[-1]['paren_depth'] += 1
            elif loop_stack and loop_stack[-1]['phase'] == 'stmt':
                loop_stack[-1]['stmt_paren'] += 1
            if decl_ctx:
                decl_ctx['paren'] += 1
        elif tok == ')':
            if decl_ctx and decl_ctx['paren'] > 0:
                decl_ctx['paren'] -= 1
            if loop_stack and loop_stack[-1]['phase'] == 'header':
                loop_stack[-1]['paren_depth'] -= 1
                if loop_stack[-1]['paren_depth'] == 0:
                    loop_stack[-1]['phase'] = 'await_body'
                    if decl_ctx and decl_ctx['in_for'] and decl_top_level():
                        decl_ctx = None
            elif loop_stack and loop_stack[-1]['phase'] == 'stmt' and loop_stack[-1]['stmt_paren'] > 0:
                loop_stack[-1]['stmt_paren'] -= 1
            pending_for = False
        elif tok == '{':
            if decl_ctx:
                decl_ctx['brace'] += 1
            elif loop_stack and loop_stack[-1]['phase'] == 'stmt':
                loop_stack[-1]['stmt_brace'] += 1
            block_scope = new_scope(current_scope())
            active_scopes.append(block_scope)
            if loop_stack and loop_stack[-1]['phase'] == 'await_body':
                loop_stack[-1]['phase'] = 'block'
                loop_stack[-1]['body_block'] = block_scope
            pending_for = False
        elif tok == '}':
            if decl_ctx and decl_ctx['brace'] > 0:
                decl_ctx['brace'] -= 1
            elif loop_stack and loop_stack[-1]['phase'] == 'stmt' and loop_stack[-1]['stmt_brace'] > 0:
                loop_stack[-1]['stmt_brace'] -= 1
            popped_scope = active_scopes.pop() if len(active_scopes) > 1 else 0
            if (loop_stack and loop_stack[-1]['phase'] == 'block'
                    and popped_scope == loop_stack[-1]['body_block']):
                active_scopes.pop()
                loop_stack.pop()
            pending_for = False
        elif tok == '[':
            if decl_ctx:
                decl_ctx['bracket'] += 1
            elif loop_stack and loop_stack[-1]['phase'] == 'stmt':
                loop_stack[-1]['stmt_bracket'] += 1
            pending_for = False
        elif tok == ']':
            if decl_ctx and decl_ctx['bracket'] > 0:
                decl_ctx['bracket'] -= 1
            elif loop_stack and loop_stack[-1]['phase'] == 'stmt' and loop_stack[-1]['stmt_bracket'] > 0:
                loop_stack[-1]['stmt_bracket'] -= 1
            pending_for = False
        elif tok == ',' and decl_ctx and decl_top_level():
            decl_ctx['expect_name'] = True
            pending_for = False
        elif tok == ';':
            if decl_ctx and decl_top_level():
                decl_ctx = None
            if (loop_stack and loop_stack[-1]['phase'] == 'stmt'
                    and not any(loop_stack[-1][k] for k in ('stmt_paren', 'stmt_brace', 'stmt_bracket'))):
                active_scopes.pop()
                loop_stack.pop()
            pending_for = False
        else:
            pending_for = False

        if tok == '.':
            dot_count += 1
        else:
            dot_count = 0
        prev_dot = (tok == '.' and dot_count == 1)

    subtree_short_idents = {}

    def fold_short_idents(scope_id):
        names = set(scope_short_direct.get(scope_id, set()))
        for child_id in scopes[scope_id]['children']:
            names |= fold_short_idents(child_id)
        subtree_short_idents[scope_id] = names
        return names

    fold_short_idents(0)

    tin = {}
    tout = {}
    clock = 0

    def index_scopes(scope_id):
        nonlocal clock
        tin[scope_id] = clock
        clock += 1
        for child_id in scopes[scope_id]['children']:
            index_scopes(child_id)
        tout[scope_id] = clock

    index_scopes(0)

    def scope_contains(root_id, child_id):
        return tin[root_id] <= tin[child_id] < tout[root_id]

    def binding_used_in_subtree(binding_id, scope_id):
        return any(scope_contains(scope_id, use_scope)
                   for use_scope in bindings[binding_id]['use_scopes'])

    global_targets = set(rename_map.values())
    binding_targets = {}

    def assign_scope(scope_id, blocked_from_ancestors):
        used = set(global_targets)
        used |= subtree_short_idents.get(scope_id, set())
        used |= blocked_from_ancestors

        local_bindings = [
            binding_id for binding_id in scopes[scope_id]['bindings']
            if bindings[binding_id]['renamable']
        ]
        local_bindings.sort(
            key=lambda binding_id: (
                -bindings[binding_id]['count'],
                bindings[binding_id]['name'],
                bindings[binding_id]['decl_pos'],
            )
        )

        target_iter = _iter_local_targets(used)
        for binding_id in local_bindings:
            target = next(target_iter)
            binding_targets[binding_id] = target
            used.add(target)

        for child_id in scopes[scope_id]['children']:
            child_blocked = set(blocked_from_ancestors)
            for binding_id in local_bindings:
                if binding_used_in_subtree(binding_id, child_id):
                    child_blocked.add(binding_targets[binding_id])
            assign_scope(child_id, child_blocked)

    assign_scope(0, set())

    local_token_renames = {}
    for pos, binding_id in token_bindings.items():
        target = binding_targets.get(binding_id)
        if target is not None:
            local_token_renames[pos] = target
    return local_token_renames


def minify_js(code, rename_map):
    """Strip comments, rename identifiers, collapse whitespace.

    Uses a single-pass tokenizer so string literals are never modified.
    Whitespace is dropped and re-inserted only where required between
    adjacent word tokens (identifiers/keywords/numbers).
    Scope-aware: let/const variables get short names reused across scopes.
    """
    # Build scope-aware renames for let/const declarations
    local_token_renames = _build_scope_renames(code, rename_map)

    out = []
    prev_word = False
    prev_dot = False
    dot_count = 0

    for kind, tok, pos in _iter_js_tokens(code):
        if kind in ('str_dq', 'str_sq', 'template'):
            out.append(tok)
            prev_word = False
            prev_dot = False
            dot_count = 0
        elif kind in ('line_comment', 'block_comment'):
            pass  # strip
        elif kind == 'ident':
            if prev_dot:
                # Property access — don't rename
                out_tok = tok
            else:
                out_tok = local_token_renames.get(pos, rename_map.get(tok, tok))
            if prev_word:
                out.append(' ')
            out.append(out_tok)
            prev_word = True
            prev_dot = False
            dot_count = 0
        elif kind == 'num':
            if prev_word:
                out.append(' ')
            out.append(tok)
            prev_word = True
            prev_dot = False
            dot_count = 0
        elif kind == 'ws':
            pass  # strip; spacing re-added by prev_word logic
        else:
            out.append(tok)
            prev_word = False
            if tok == '.':
                dot_count += 1
            else:
                dot_count = 0
            prev_dot = (tok == '.' and dot_count == 1)

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
    # Read the JS payload and CSS
    with open(os.path.join(SRC_DIR, 'kanjimap.js')) as f:
        js_payload = f.read()
    with open(os.path.join(SRC_DIR, 'styles.css')) as f:
        css = f.read()

    # Minify CSS: strip comments, collapse whitespace, remove unnecessary spaces
    import re as _re_css
    css = _re_css.sub(r'/\*.*?\*/', '', css, flags=_re_css.DOTALL)  # strip comments
    css = _re_css.sub(r'\s+', ' ', css)          # collapse whitespace
    css = _re_css.sub(r'\s*([{}:;,>+~])\s*', r'\1', css)  # remove space around punctuation
    css = css.strip()

    # Inject minified CSS into JS
    js_payload = js_payload.replace('CSS_PLACEHOLDER', css)

    # Compute models from snapshot and inject into JS
    import json as _json
    from reencode_bac import (compute_models, M_CELL, M_KT0, M_KT1, M_ONKUN,
                               M_TDP, M_D1K, M_D1O, M_D2_0, M_D2_1,
                               M_EXTRA, M_OKURI)
    with open(os.path.join(SRC_DIR, 'data.json')) as f:
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

    # Minify: rename identifiers + strip whitespace/comments
    _rmap = compute_rename_map(js_payload)
    js_minified = minify_js(js_payload, _rmap)

    # Validate: check no symbolic placeholders remain in minified output
    # (checked after minification so comments can freely reference placeholders)
    import re as _re
    KNOWN_PLACEHOLDERS = ['KD', 'KL', 'CP', 'K1', 'OK', 'DO', 'DK', 'D0', 'D1',
                          'EF', 'OF', 'KP', 'TP']
    for ph in KNOWN_PLACEHOLDERS:
        if _re.search(r'\b' + ph + r'\b', js_minified):
            print(f"ERROR: placeholder {ph!r} was not replaced in JS", file=sys.stderr)
            sys.exit(1)
    print(f"JS: {len(js_payload)} bytes → minified: {len(js_minified)} bytes", file=sys.stderr)

    # Write minified JS for reference
    with open(os.path.join(ROOT_DIR, 'build', 'kanjimap_processed.js'), 'w') as f:
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
        'B=s=>{i=0,v=0,o=[];do{'
        'while(v<2**24&&i<s.length)v=v*93+(s.charCodeAt(i++)+26)*58/59-57|0;'
        'o.push(v&255);v>>=8}while(v>1);return o};\n'
        # Decode F from base-93, decompress, eval payload
        '(async()=>{'
        'a=new Uint8Array(B(F));'
        's=new Blob([a]).stream().pipeThrough(new DecompressionStream("deflate-raw"));'
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
