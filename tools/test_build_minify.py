#!/usr/bin/env python3

import re
import shutil
import subprocess
import sys

import build


def minify(code):
    return build.minify_js(code, build.compute_rename_map(code))


def run_js(code):
    return subprocess.run(
        ['node', '-e', code],
        capture_output=True,
        text=True,
        check=False,
    )


def assert_same_runtime(label, code):
    original = run_js(code)
    minified = run_js(minify(code))
    if (original.returncode, original.stdout, original.stderr) != (
        minified.returncode, minified.stdout, minified.stderr
    ):
        raise AssertionError(
            f'{label}: runtime mismatch\n'
            f'original rc={original.returncode} stdout={original.stdout!r} stderr={original.stderr!r}\n'
            f'minified rc={minified.returncode} stdout={minified.stdout!r} stderr={minified.stderr!r}\n'
            f'code={code}\n'
            f'minified={minify(code)}'
        )


def assert_matches(label, code, pattern):
    result = minify(code)
    if not re.search(pattern, result):
        raise AssertionError(
            f'{label}: expected /{pattern}/ in {result}'
        )


def assert_contains(label, code, needle):
    result = minify(code)
    if needle not in result:
        raise AssertionError(
            f'{label}: expected {needle!r} in {result}'
        )


def assert_rename_map(label, code, expected_present=(), expected_absent=()):
    rename_map = build.compute_rename_map(code)
    for name in expected_present:
        if name not in rename_map:
            raise AssertionError(
                f'{label}: expected {name!r} in rename map, got {rename_map}'
            )
    for name in expected_absent:
        if name in rename_map:
            raise AssertionError(
                f'{label}: expected {name!r} to stay out of rename map, got {rename_map}'
            )


def main():
    if shutil.which('node'):
        assert_same_runtime(
            'for-let-header-and-body',
            'for(let outerIndex=0;outerIndex<3;outerIndex++)console.log(outerIndex)',
        )
        assert_same_runtime(
            'outer-binding-visible-in-child',
            'let outerCount=0;{console.log(outerCount);let innerCount=1;console.log(innerCount)}',
        )
    else:
        print('node not found; skipping runtime equivalence checks')
    assert_matches(
        'sibling-block-reuse',
        'let outerCount=0;{let innerCount=1;console.log(innerCount)}{let otherCount=2;console.log(otherCount)}',
        r'^\s*let i=0;\{let i=1;console\.log\(i\)\}\{let i=2;console\.log\(i\)\}\s*$',
    )
    assert_matches(
        'local-frequency-order',
        '{let zName=0;let aName=0;zName++;zName++;aName++;console.log(zName,aName)}',
        r'^\s*\{let i=0,j=0;i\+\+;i\+\+;j\+\+;console\.log\(i,j\)\}\s*$',
    )
    assert_contains(
        'string-literals-not-rewritten',
        'console.log("true false ;}")',
        '"true false ;}"',
    )
    assert_contains(
        'template-literals-not-rewritten',
        'console.log(`true false ;}`)',
        '`true false ;}`',
    )
    assert_contains(
        'strings-not-touched-by-let-merge',
        'console.log("let a=1;let b=2")',
        '"let a=1;let b=2"',
    )
    assert_contains(
        'decimal-leading-zero-trim',
        'console.log(0.5,0.95,1.0)',
        'console.log(.5,.95,1)',
    )
    assert_contains(
        'boolean-literals-rewritten',
        'console.log(true,false)',
        'console.log(!0,!1)',
    )
    assert_matches(
        'property-access-not-rewritten',
        'console.log(obj.true,obj.false)',
        r'console\.log\([A-Za-z_$][A-Za-z0-9_$]*\.true,[A-Za-z_$][A-Za-z0-9_$]*\.false\)',
    )
    assert_rename_map(
        'nested-lexical-bindings-stay-out-of-global-map',
        'let globalState=0;use(globalState);{let localCounter=0;localCounter++;localCounter++;use(localCounter)}',
        expected_present=('globalState',),
        expected_absent=('localCounter',),
    )
    css = build._minify_css_numbers('opacity:0.10;zoom:1.0;scale:0.95;')
    if css != 'opacity:.10;zoom:1;scale:.95;':
        raise AssertionError(f'css-number-minify: got {css!r}')
    css_input = '.alpha.beta{color:red}body.dark .alpha{}'
    js_input = (
        "el.className='alpha beta';"
        "el.classList.add('dark');"
        "el.querySelector('.alpha.beta')"
    )
    class_map = build.compute_class_rename_map(css_input, js_input)
    css_output = build._rewrite_css_classes(css_input, class_map)
    js_output = build._rewrite_js_class_strings(js_input, class_map)
    expected_css = (
        f".{class_map['alpha']}.{class_map['beta']}{{color:red}}"
        f"body.{class_map['dark']} .{class_map['alpha']}{{}}"
    )
    expected_js = (
        f"el.className='{class_map['alpha']} {class_map['beta']}';"
        f"el.classList.add('{class_map['dark']}');"
        f"el.querySelector('.{class_map['alpha']}.{class_map['beta']}')"
    )
    if css_output != expected_css:
        raise AssertionError(f'class-rename-css: got {css_output!r}')
    if js_output != expected_js:
        raise AssertionError(f'class-rename-js: got {js_output!r}')
    html_js_input = "document.body.innerHTML='<div class=\"alpha beta\"></div>'"
    html_js_output = build._rewrite_js_class_strings(html_js_input, class_map)
    expected_html_js = (
        "document.body.innerHTML="
        f"'<div class=\"{class_map['alpha']} {class_map['beta']}\"></div>'"
    )
    if html_js_output != expected_html_js:
        raise AssertionError(f'class-rename-html: got {html_js_output!r}')
    custom_prop_input = (
        "body{--font-serif-ja:serif;--font-sans-ja:sans-serif;"
        "font-family:var(--font-serif-ja);border-color:var(--font-sans-ja,#000)}"
    )
    custom_prop_map = build.compute_css_custom_prop_rename_map(custom_prop_input)
    custom_prop_output = build._rewrite_css_custom_properties(custom_prop_input, custom_prop_map)
    expected_custom_prop_output = (
        f"body{{--{custom_prop_map['font-serif-ja']}:serif;--{custom_prop_map['font-sans-ja']}:sans-serif;"
        f"font-family:var(--{custom_prop_map['font-serif-ja']});"
        f"border-color:var(--{custom_prop_map['font-sans-ja']},#000)}}"
    )
    if custom_prop_output != expected_custom_prop_output:
        raise AssertionError(f'custom-prop-rename-css: got {custom_prop_output!r}')
    custom_prop_js_input = (
        "document.body.style.setProperty('--fs',1);"
        "document.body.style.setProperty('--font-serif-ja','serif')"
    )
    custom_prop_js_map = build.compute_css_custom_prop_rename_map(
        "body{--fs:1;--font-serif-ja:serif;font-size:calc(12px*var(--fs));font-family:var(--font-serif-ja)}"
    )
    custom_prop_js_output = build._rewrite_js_custom_prop_strings(
        custom_prop_js_input,
        custom_prop_js_map,
    )
    expected_custom_prop_js_output = (
        f"document.body.style.setProperty('--{custom_prop_js_map['fs']}',1);"
        f"document.body.style.setProperty('--{custom_prop_js_map['font-serif-ja']}','serif')"
    )
    if custom_prop_js_output != expected_custom_prop_js_output:
        raise AssertionError(f'custom-prop-rename-js: got {custom_prop_js_output!r}')
    print('build.py minify regression tests passed')


if __name__ == '__main__':
    try:
        main()
    except AssertionError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
