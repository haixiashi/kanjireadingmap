// --- Page setup (title, body skeleton, viewport meta, CSS) ---
document.title = '漢字読み方表';
document.body.innerHTML = '<div class="viewport"><table id="grid"><tbody id="tbody"></tbody></table></div>';

// CSS is kept minified as a string — gzip compresses it efficiently as-is.
document.head.innerHTML += '<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"><style>*{box-sizing:border-box;margin:0;padding:0;-webkit-text-size-adjust:none;text-size-adjust:none}body{font-family:sans-serif;background:#fff}.viewport{overflow:auto;width:100vw;height:100vh;scrollbar-width:none;cursor:grab;user-select:none}.viewport::-webkit-scrollbar{display:none}.viewport.dragging{cursor:grabbing}table{border-collapse:collapse}td{border:1px solid var(--b,#ccc);padding:2px 4px;background:#fff;vertical-align:top;font-size:calc(10px * var(--fs,1));width:128px;min-width:128px;height:128px;overflow:hidden;position:relative;contain:strict}.kanji-group.large{font-size:calc(16px * var(--fs,1))}.kanji-group.large ruby{font-size:calc(26px * var(--fs,1))}.kanji-group.large rt{font-size:calc(11px * var(--fs,1))}td.first-col{border-right:3px solid #000}.watermark{position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;display:flex;align-items:center;justify-content:center;font-size:38px;color:#999;opacity:0.10;font-weight:bold}.kanji-group{display:inline-block;margin:1px 2px;white-space:nowrap;padding:1px 2px;border-radius:3px}ruby{font-size:calc(12px * var(--fs,1))}rt{font-size:calc(6px * var(--fs,1));color:#888}.content{position:absolute;top:2px;left:4px;right:4px;bottom:2px;overflow:hidden}td.has-more::after{content:"…";position:absolute;bottom:1px;right:3px;font-size:calc(10px * var(--fs,1));color:#aaa;pointer-events:none}body.dark td.has-more::after{color:#777}.hover-card{position:fixed;z-index:5;background:#fff;border:2px solid #37d;pointer-events:none;border-radius:6px;padding:2px 4px;font-size:calc(10px * var(--fs,1));transform-origin:center center;overflow:hidden;box-shadow:0 2px 12px #0004}.tier5{color:#373}.tier4{color:#693}.tier3{color:#fa2}.tier2{color:#e50}.tier1{color:#b22}.group-left{border-left:2.5px solid var(--g,#555)}.group-top{border-top:2.5px solid var(--g,#555)}.group-right{border-right:2.5px solid var(--g,#555)}.group-bottom{border-bottom:2.5px solid var(--g,#555)}.fixed-btn{position:fixed;bottom:16px;z-index:10;height:44px;border-radius:6px;border:1px solid #999;background:#fff;cursor:pointer;display:flex;align-items:center;justify-content:center;line-height:1;padding:0;transition:background 0.2s,border-color 0.2s}.fixed-btn:hover{background:#ccc}.theme-toggle{right:16px;width:44px;font-size:24px}.reading-toggle{right:68px;width:44px;font-size:22px;font-weight:bold}body.kun-only .kanji-group.on{display:none}body.on-only .kanji-group.kun{display:none}body.dark{background:#222;--b:#444;--g:#999}body.dark td{background:#222;color:#ddd}body.dark td.first-col{border-right-color:#aaa}body.dark .watermark{color:#666;opacity:0.18}body.dark rt{color:#999}body.dark .hover-card{background:#222;border-color:#59f}body.dark .tier5{color:#6b6}body.dark .tier4{color:#9c6}body.dark .tier3{color:#fe5}body.dark .tier2{color:#f90}body.dark .tier1{color:#e55}body.dark .fixed-btn{background:#222;border-color:#666;color:#ddd}body.dark .fixed-btn:hover{background:#444}</style>';
decodeCell = (() => {
    // --- Arithmetic decoder state ---
    let bitString = B(D);
    let bitPos = 0;
    const RANGE_TOP = 2 ** 31;
    const RANGE_QUARTER = RANGE_TOP / 2;
    const RANGE_MODULUS = RANGE_TOP * 2;
    let rangeLow = 0;
    let rangeHigh = RANGE_MODULUS - 1;
    let rangeValue = 0;
    let codepoint = 19968;          // U+4E00 = 一, first kanji
    let kanjiTable = String.fromCharCode(codepoint);

    // Prime the decoder with 32 bits
    for (let i = 0; i < 32; i++)
        rangeValue = (+bitString[bitPos++] + rangeValue * 2) % RANGE_MODULUS;

    const normalize = () => {
        while (1) {
            if (rangeLow >= RANGE_TOP) {
                rangeLow -= RANGE_TOP; rangeHigh -= RANGE_TOP; rangeValue -= RANGE_TOP;
            } else if (rangeHigh < RANGE_TOP) {
                // nothing
            } else if (rangeLow >= RANGE_QUARTER && rangeHigh < 3 * RANGE_QUARTER) {
                rangeLow -= RANGE_QUARTER; rangeHigh -= RANGE_QUARTER; rangeValue -= RANGE_QUARTER;
            } else break;
            rangeLow = rangeLow * 2 % RANGE_MODULUS;
            rangeHigh = (rangeHigh * 2 + 1) % RANGE_MODULUS;
            rangeValue = (+bitString[bitPos++] + rangeValue * 2) % RANGE_MODULUS;
        }
    };

    // Decode one symbol from a 999-scale cumulative frequency model.
    // Pass inner boundary values only; 0 and 999 are implicit.
    const decode = (...innerBoundaries) => {
        let range = rangeHigh - rangeLow + 1;
        let sym = 0;
        let base = rangeLow;
        while (sym < innerBoundaries.length &&
               base + range * innerBoundaries[sym] / 999 < rangeValue + 1)
            sym++;
        rangeLow = sym > 0
            ? base + Math.trunc(range * innerBoundaries[sym - 1] / 999)
            : base;
        if (sym < innerBoundaries.length)
            rangeHigh = base + Math.trunc(range * innerBoundaries[sym] / 999) - 1;
        normalize();
        return sym;
    };

    // Decode a uniform symbol in 0..n-1
    const decodeUniform = n => {
        let step = Math.trunc((rangeHigh - rangeLow + 1) / n);
        let sym = Math.trunc((rangeValue - rangeLow) / step);
        if (sym >= n) sym = n - 1;
        rangeLow += step * sym;
        if (sym < n - 1) rangeHigh = rangeLow + step - 1;
        normalize();
        return sym;
    };

    // --- Section 1: KT (kanji table) — delta-encoded codepoints ---
    for (let i = 0; i < KL - 1; i++) {
        let deltaRange = 2 << decode(KD);          // exp-Golomb bucket
        codepoint += decodeUniform(deltaRange) + deltaRange - 1;
        kanjiTable += String.fromCharCode(codepoint);
    }

    // --- Section 2: Kana probability table (82 symbols, k² deltas) ---
    let kanaCumFreq = [];
    let kanaFreqAcc = 0;
    for (let i = 0; i < 81; i++) {
        let k = decodeUniform(14);
        kanaCumFreq[i] = kanaFreqAcc += k * k;
    }

    // --- Section 3: KN — 45 kana for grid row/col layout ---
    let kanaGridCodepoint = 0x3042;  // あ
    kanaGrid = String.fromCharCode(kanaGridCodepoint);
    for (let i = 0; i < 44; i++)
        kanaGrid += String.fromCharCode(kanaGridCodepoint += decodeUniform(4) + 1);

    // --- Cell decoder (returned function) ---
    return cellKana => {
        // Non-first-column cells may be empty
        if (cellKana[1] && !decode(CP)) return [];

        let entries = [];
        let prevTier = 5;

        for (;;) {
            // End of cell? (model conditioned on prevTier)
            if (decode(KP[prevTier - 1])) return entries;

            // Decode one kanji group (1+ kanji sharing the same reading/tier)
            let kanjiGroup = [];
            kanjiGroup.push(kanjiTable[decodeUniform(KL)]);
            while (!decode(K1))
                kanjiGroup.push(kanjiTable[decodeUniform(KL)]);

            let isOn = decode(OK);                          // 0=kun, 1=on
            prevTier -= decode(...TP[prevTier - 1]);        // tier delta
            let tier = prevTier;

            // Variant offsets: d1 for first kana char, d2 for second
            let firstKanaVariant = decode(isOn ? DO : DK);
            let secondKanaVariant = (firstKanaVariant ? decode(D1) : decode(D0)) - 1;
            let variantOffsets = [firstKanaVariant, secondKanaVariant];

            // Reconstruct reading from cell position + katakana shift + variant
            let katakanaShift = isOn * 96;
            let reading = cellKana.replace(/./g, (c, idx) =>
                String.fromCharCode(c.charCodeAt(0) + katakanaShift + variantOffsets[idx]));

            // Extra kana beyond the cell prefix
            while (decode(EF))
                reading += String.fromCharCode(decode(...kanaCumFreq) + 0x3042 + katakanaShift);

            // Okurigana (kun-yomi only)
            let okurigana = '';
            while (!isOn && decode(OF))
                okurigana += String.fromCharCode(decode(...kanaCumFreq) + 0x3042);

            kanjiGroup.map(kanji => entries.push([kanji, reading, tier, okurigana, isOn]));
        }
    };
})();
makeEntrySpan = (kanji, reading, tier, okurigana, isOn) => {
    let span = document.createElement('span');
    span.className = 'kanji-group';
    span.classList.add(isOn ? 'on' : 'kun');
    if (tier > 0) span.classList.add('tier' + tier);
    let rubyEl = document.createElement('ruby');
    rubyEl.textContent = kanji;
    let rtEl = document.createElement('rt');
    rtEl.textContent = reading;
    rubyEl.append(rtEl);
    span.append(rubyEl);
    if (okurigana) span.append(document.createTextNode(okurigana));
    return span;
};
(() => {
    const colKana = ['', ...kanaGrid];
    const rowKana = [...kanaGrid].slice(0, -1);
    // Column/row indices where thick group borders are drawn
    const colBorders = [0, 6, 11, 16, 21, 26, 31, 36, 39, 44];
    const rowBorders = colBorders.map(v => v - !!v);
    const tbody = document.getElementById('tbody');

    rowKana.forEach((rowLabel, rowIdx) => {
        let tr = document.createElement('tr');

        colKana.forEach((colLabel, colIdx) => {
            let td = document.createElement('td');

            // Group border CSS classes
            if (colIdx === 0)                   td.classList.add('first-col');
            if (colBorders.includes(colIdx))    td.classList.add('group-left');
            if (colIdx === colKana.length - 1)  td.classList.add('group-right');
            if (rowBorders.includes(rowIdx))    td.classList.add('group-top');
            if (rowIdx === rowKana.length - 1)  td.classList.add('group-bottom');

            let entries = decodeCell(rowLabel + colLabel);
            td._entries = entries;

            if (!entries.length) {
                td.classList.add('empty');
            } else {
                let contentDiv = document.createElement('div');
                contentDiv.className = 'content';
                entries.forEach((entry, i) => {
                    let span = makeEntrySpan(...entry);
                    if (!i) span.classList.add('large');
                    contentDiv.append(span);
                });
                td.append(contentDiv);
            }

            // Watermark shows the cell's kana label (hiragana or katakana)
            let watermark = document.createElement('div');
            watermark.className = 'watermark';
            watermark.dataset.hiragana = rowLabel + colLabel;
            watermark.dataset.katakana = [...rowLabel + colLabel]
                .map(c => String.fromCharCode(c.charCodeAt(0) + 96)).join('');
            watermark.textContent = rowLabel + colLabel;
            td.append(watermark);

            tr.append(td);
        });

        tbody.append(tr);
    });
})();
(() => {
    // --- Globals used across functions ---
    storage  = localStorage;
    viewport = document.querySelector('.viewport');
    table    = document.getElementById('grid');

    // --- Reading mode toggle (漢=both / 訓=kun-only / 音=on-only) ---
    readingBtn = document.createElement('button');
    const modes      = ['both', 'kun-only', 'on-only'];
    const modeLabels = '漢訓音';
    modeIdx = modes.indexOf(storage.getItem('rm'));
    if (modeIdx < 0) modeIdx = 0;
    readingBtn.className  = 'fixed-btn reading-toggle';
    readingBtn.textContent = modeLabels[modeIdx];
    if (modes[modeIdx] !== 'both') document.body.classList.add(modes[modeIdx]);

    updateReadings = () => {
        const hiddenClass = modes[modeIdx] === 'kun-only' ? 'on'
                          : modes[modeIdx] === 'on-only'  ? 'kun' : '';
        const isKatakana  = modes[modeIdx] === 'on-only';

        document.querySelectorAll('.watermark').forEach(wm => {
            wm.textContent = isKatakana ? wm.dataset.katakana : wm.dataset.hiragana;
        });

        document.querySelectorAll('#tbody td').forEach(td => {
            let spans = td.querySelectorAll('.kanji-group');
            if (!spans.length) return;

            // Count visible spans and update empty state
            let visibleCount = 0;
            for (let span of spans)
                if (!span.classList.contains(hiddenClass)) visibleCount++;
            if (!visibleCount) td.classList.add('empty');
            else td.classList.remove('empty');

            // Apply .large to first visible span only
            let largeAssigned = 0;
            for (let span of spans) {
                if (span.classList.contains(hiddenClass)) {
                    span.classList.remove('large');
                    continue;
                }
                if (largeAssigned < 1) span.classList.add('large');
                else span.classList.remove('large');
                largeAssigned++;
            }
        });
        if (typeof clipCellEntries === 'function') clipCellEntries();
    };

    readingBtn.addEventListener('click', () => {
        if (modes[modeIdx] !== 'both') document.body.classList.remove(modes[modeIdx]);
        modeIdx = (modeIdx + 1) % 3;
        if (modes[modeIdx] !== 'both') document.body.classList.add(modes[modeIdx]);
        readingBtn.textContent = modeLabels[modeIdx];
        storage.setItem('rm', modes[modeIdx]);
        updateReadings();
    });
    updateReadings();
    document.body.append(readingBtn);

    // --- Theme toggle (light / dark) ---
    themeBtn = document.createElement('button');
    themeBtn.className   = 'fixed-btn theme-toggle';
    themeBtn.textContent = '☾';
    document.body.append(themeBtn);
    if (storage.getItem('dk') !== '0') document.body.classList.add('dark');
    if (document.body.classList.contains('dark')) themeBtn.textContent = '☀';
    themeBtn.addEventListener('click', () => {
        document.body.classList.toggle('dark');
        let isDark = document.body.classList.contains('dark');
        themeBtn.textContent = isDark ? '☀' : '☾';
        storage.setItem('dk', isDark ? '1' : '0');
    });

    // --- Hover card ---
    hoverCell = null;
    hoverCard = document.createElement('div');
    hoverCard.className    = 'hover-card';
    hoverCard.style.display = 'none';
    document.body.append(hoverCard);

    showHover = td => {
        hoverCell = td;
        hoverCard.innerHTML = '';
        let wm = td.querySelector('.watermark');
        if (wm) hoverCard.append(wm.cloneNode(true));

        const hiddenClass = document.body.classList.contains('kun-only') ? 'on'
                          : document.body.classList.contains('on-only')  ? 'kun' : '';
        let entries = td._entries || [];
        let visible = entries.filter(e => !hiddenClass || (e[4] ? 'on' : 'kun') !== hiddenClass);
        visible.forEach((e, i) => {
            let span = makeEntrySpan(...e);
            if (!i) span.classList.add('large');
            hoverCard.append(span);
        });

        // Size and position the card centered on the tapped cell.
        // transformScale is clamped to at least 1.2 so the card never appears
        // smaller than it would at zoom=1, regardless of zoom level.
        let transformScale = Math.max(scale, 1) * 1.2;
        let rect  = td.getBoundingClientRect();
        // cellW is in unscaled CSS px (the card is sized before the transform is applied)
        let cellW = rect.width / scale;
        hoverCard.style.display = 'block';
        hoverCard.style.width   = cellW + 'px';
        hoverCard.style.height  = 'auto';

        // Grow toward a square that fits all content
        let scrollH = hoverCard.scrollHeight + 8;
        let side    = Math.sqrt(cellW * scrollH);
        side = Math.max(side, cellW);
        hoverCard.style.width  = side + 'px';
        hoverCard.style.height = 'auto';
        scrollH = hoverCard.scrollHeight + 8;
        let sz = Math.max(side, scrollH);
        hoverCard.style.width  = sz + 'px';
        hoverCard.style.height = sz + 'px';

        // transform-origin is center center, so left/top position the pre-transform center.
        // Apparent (post-transform) size = sz * transformScale; center on the cell center.
        hoverCard.style.transform = 'scale(' + transformScale + ')';
        let cx = rect.left + rect.width  / 2;
        let cy = rect.top  + rect.height / 2;
        hoverCard.style.left = cx - sz / 2 + 'px';
        hoverCard.style.top  = cy - sz / 2 + 'px';
    };

    // --- Layout constants and state ---
    let fsCap = 1;  // set after table render; used by applyScale
    const TABLE_MARGIN = 172;    // extra space around table for panning headroom
    scale = 1;
    zooming = 0;
    lastX = lastY = dragging = velX = velY = lastTime = animFrame = didDrag = 0;

    // Wrap table in a relative-positioned div so absolute positioning works
    wrapper = document.createElement('div');
    wrapper.style.position = 'relative';
    table.parentNode.insertBefore(wrapper, table);
    wrapper.append(table);
    table.style.position      = 'absolute';
    table.style.top           = TABLE_MARGIN + 'px';
    table.style.left          = TABLE_MARGIN + 'px';
    table.style.transformOrigin = '0 0';
    table.style.willChange    = 'transform';
    tableW = table.offsetWidth;
    tableH = table.offsetHeight;
    resetTimer = 0;

    // --- Scale / zoom ---
    applyScale = () => {
        table.style.transform = 'scale(' + scale + ')';
        if (!zooming) {
            let fontScale = scale < 1.5 ? Math.min(1.5 / scale, fsCap) : 1;
            document.body.style.setProperty('--fs', fontScale);
        }
        let contentW = tableW * scale + TABLE_MARGIN * 2;
        let contentH = tableH * scale + TABLE_MARGIN * 2;
        let wrapW = Math.max(contentW, viewport.clientWidth);
        let wrapH = Math.max(contentH, viewport.clientHeight);
        table.style.left      = (wrapW - tableW * scale) / 2 + 'px';
        table.style.top       = (wrapH - tableH * scale) / 2 + 'px';
        wrapper.style.width   = wrapW + 'px';
        wrapper.style.height  = wrapH + 'px';
    };

    // Hide spans whose bottom edge is clipped by their .content container.
    // Called once at init and again after each zoom settles.
    clipCellEntries = () => {
        // content height = td(128px) - top(2px) - bottom(8px) = 118px, fixed by CSS.
        // Batch all writes before all reads to avoid layout thrashing:
        // 1. Reset all visibility (batch write — 1 layout invalidation)
        // 2. Read all offsets (batch read — 1 forced layout, then cached)
        // 3. Apply visibility:hidden + has-more (batch write)
        const contentH = 124;  // td(128px) - top(2px) - bottom(2px)
        const allContent = Array.from(document.querySelectorAll('.content'));
        const allSpans = allContent.map(c => Array.from(c.querySelectorAll('.kanji-group')));

        // Batch write: reset all
        allSpans.forEach(spans => spans.forEach(sp => sp.style.visibility = ''));

        // Batch read: measure all (single layout calculation)
        const overflows = allSpans.map(spans =>
            spans.map(sp => sp.offsetTop + sp.offsetHeight > contentH)
        );

        // Batch write: apply results
        allContent.forEach((content, ci) => {
            let anyHidden = false;
            allSpans[ci].forEach((sp, si) => {
                if (overflows[ci][si]) { sp.style.visibility = 'hidden'; anyHidden = true; }
            });
            content.parentElement.classList.toggle('has-more', anyHidden);
        });
    };

    // Reset willChange after a zoom gesture to free compositor resources
    resetWillChange = () => {
        zooming = 0;
        applyScale();
        table.style.willChange = 'auto';
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                table.style.willChange = 'transform';
                clipCellEntries();
            });
        });
    };
    scheduleWillChangeReset = () => {
        clearTimeout(resetTimer);
        resetTimer = setTimeout(resetWillChange, 150);
    };
    applyScale();

    // Reposition hover card after scroll/drag, throttled to one rAF per frame
    let hoverPending = 0;
    schedHover = () => {
        if (hoverCell && !hoverPending) {
            hoverPending = 1;
            requestAnimationFrame(() => { if (hoverCell) showHover(hoverCell); hoverPending = 0; });
        }
    };

    // --- Drag / pan / coast ---
    startDrag = (x, y) => {
        cancelAnimationFrame(animFrame);
        dragging = 1; didDrag = 0;
        lastX = x; lastY = y;
        velX = velY = 0;
        lastTime = performance.now();
        viewport.classList.add('dragging');
    };

    moveDrag = (x, y) => {
        let now = performance.now();
        let dt  = now - lastTime || 1;
        let dx  = x - lastX, dy = y - lastY;
        if (Math.abs(dx) > 4 || Math.abs(dy) > 4) didDrag = 1;
        velX = dx / dt * 16;
        velY = dy / dt * 16;
        viewport.scrollLeft -= dx;
        viewport.scrollTop  -= dy;
        lastX = x; lastY = y;
        lastTime = now;
        schedHover();
    };

    coast = () => {
        if (Math.abs(velX) < 0.5 && Math.abs(velY) < 0.5) return;
        viewport.scrollLeft -= velX;
        viewport.scrollTop  -= velY;
        velX *= 0.95;
        velY *= 0.95;
        schedHover();
        animFrame = requestAnimationFrame(coast);
    };

    endDrag = () => {
        dragging = 0;
        viewport.classList.remove('dragging');
        animFrame = requestAnimationFrame(coast);
    };

    // --- Mouse events ---
    viewport.addEventListener('mousedown', e => startDrag(e.clientX, e.clientY));
    document.addEventListener('mousemove', e => {
        if (dragging) moveDrag(e.clientX, e.clientY);
    });
    document.addEventListener('mouseup', () => {
        if (dragging) endDrag();
    });
    viewport.addEventListener('wheel', e => {
        e.preventDefault();
        let rect      = viewport.getBoundingClientRect();
        let mouseX    = e.clientX - rect.left + viewport.scrollLeft;
        let mouseY    = e.clientY - rect.top  + viewport.scrollTop;
        let prevScale = scale;
        scale *= e.deltaY > 0 ? 0.9 : 1 / 0.9;
        scale = Math.max(0.4, Math.min(2.5, scale));
        zooming = 1;
        applyScale();
        let scaleRatio = scale / prevScale;
        viewport.scrollLeft = mouseX * scaleRatio - (e.clientX - rect.left);
        viewport.scrollTop  = mouseY * scaleRatio - (e.clientY - rect.top);
        scheduleWillChangeReset();
    }, { passive: false });

    // --- Touch events ---
    gesture = null;
    viewport.addEventListener('touchstart', e => {
        if (e.touches.length === 2) {
            e.preventDefault();
            let a = e.touches[0], b = e.touches[1];
            gesture = {
                pinch: true,
                startDist: Math.hypot(b.clientX - a.clientX, b.clientY - a.clientY),
                cx: (a.clientX + b.clientX) / 2,
                cy: (a.clientY + b.clientY) / 2,
                startScale:  scale,
                startScrollX: viewport.scrollLeft,
                startScrollY: viewport.scrollTop,
                translateX: 0,
                translateY: 0,
            };
        } else if (e.touches.length === 1) {
            cancelAnimationFrame(animFrame);
            velX = velY = 0;
            lastTime = performance.now();
            gesture = { drag: true, x: e.touches[0].clientX, y: e.touches[0].clientY };
        }
    }, { passive: false });

    viewport.addEventListener('touchmove', e => {
        if (e.touches.length === 2 && gesture && gesture.pinch) {
            e.preventDefault();
            let a = e.touches[0], b = e.touches[1];
            let dist = Math.hypot(b.clientX - a.clientX, b.clientY - a.clientY);
            let cx   = (a.clientX + b.clientX) / 2;
            let cy   = (a.clientY + b.clientY) / 2;
            let rect = viewport.getBoundingClientRect();
            let prevScale = scale;
            scale = Math.max(0.2, Math.min(5, gesture.startScale * (dist / gesture.startDist)));
            let scaleRatio = scale / prevScale;
            let pivotX = gesture.cx - rect.left + gesture.startScrollX;
            let newCX  = cx - rect.left + gesture.startScrollX;
            let pivotY = gesture.cy - rect.top  + gesture.startScrollY;
            let newCY  = cy - rect.top  + gesture.startScrollY;
            gesture.translateX = newCX - (pivotX - gesture.translateX) * scaleRatio;
            gesture.translateY = newCY - (pivotY - gesture.translateY) * scaleRatio;
            table.style.transform = 'translate(' + gesture.translateX + 'px,' + gesture.translateY + 'px) scale(' + scale + ')';
            gesture.cx = cx; gesture.cy = cy;
            gesture.startDist = dist;
            gesture.startScale = scale;
            if (hoverCell) showHover(hoverCell);
        } else if (e.touches.length === 1 && gesture && gesture.drag) {
            e.preventDefault();
            let t   = e.touches[0];
            let now = performance.now();
            let dt  = now - lastTime || 1;
            let dx  = t.clientX - gesture.x;
            let dy  = t.clientY - gesture.y;
            velX = dx / dt * 16;
            velY = dy / dt * 16;
            viewport.scrollLeft -= dx;
            viewport.scrollTop  -= dy;
            gesture.x = t.clientX;
            gesture.y = t.clientY;
            lastTime = now;
            schedHover();
        }
    }, { passive: false });

    viewport.addEventListener('touchend', e => {
        if (gesture && gesture.drag && !e.touches.length) {
            animFrame = requestAnimationFrame(coast);
        } else if (gesture && gesture.pinch) {
            applyScale();
            viewport.scrollLeft = gesture.startScrollX - gesture.translateX;
            viewport.scrollTop  = gesture.startScrollY - gesture.translateY;
            clearTimeout(resetTimer);
            resetWillChange();
        }
        gesture = null;
    });

    // --- Scroll and click ---

    document.addEventListener('click', e => {
        if (hoverCell && !hoverCell.contains(e.target)) {
            hoverCard.style.display = 'none';
            hoverCell = null;
        }
    });

    viewport.addEventListener('click', e => {
        if (didDrag) { didDrag = 0; return; }
        let el = e.target;
        while (el && el.tagName !== 'TD') el = el.parentElement;
        if (!el || !el._entries || !el._entries.length || el.classList.contains('empty')) return;
        if (el === hoverCell) {
            hoverCard.style.display = 'none';
            hoverCell = null;
        } else {
            showHover(el);
        }
    });

    // --- Random initial scroll to a non-empty cell ---
    cells     = table.querySelectorAll('td:not(.empty)');
    startCell = cells[Math.random() * cells.length | 0];
    viewport.scrollLeft = (TABLE_MARGIN + startCell.offsetLeft + startCell.offsetWidth  / 2) * scale - viewport.clientWidth  / 2;
    viewport.scrollTop  = (TABLE_MARGIN + startCell.offsetTop  + startCell.offsetHeight / 2) * scale - viewport.clientHeight / 2;
    // Find widest first-kun and first-on span by text length (no layout reads needed)
    let widestKun = null, widestOn = null, maxKunLen = 0, maxOnLen = 0;
    document.querySelectorAll('#tbody td:not(.empty)').forEach(td => {
        const firstKun = td.querySelector('.kanji-group.kun');
        const firstOn  = td.querySelector('.kanji-group.on');
        if (firstKun) {
            const len = firstKun.textContent.length;
            if (len > maxKunLen) { maxKunLen = len; widestKun = firstKun; }
        }
        if (firstOn) {
            const len = firstOn.textContent.length;
            if (len > maxOnLen) { maxOnLen = len; widestOn = firstOn; }
        }
    });
    // Measure only the two widest candidates (2 offsetWidth reads = 1 forced layout)
    const probe = document.createElement('div');
    probe.style.cssText = 'position:absolute;left:-9999px;top:-9999px;white-space:nowrap;visibility:hidden';
    document.body.appendChild(probe);
    let maxLargeEntryWidth = 0;
    [widestKun, widestOn].forEach(span => {
        if (!span) return;
        const clone = span.cloneNode(true);
        clone.classList.add('large');
        probe.appendChild(clone);
        maxLargeEntryWidth = Math.max(maxLargeEntryWidth, clone.offsetWidth);
        probe.removeChild(clone);
    });
    document.body.removeChild(probe);
    // 128px cell minus 4px+4px .content insets minus 2px+2px .kanji-group padding = 116px usable
    fsCap = maxLargeEntryWidth > 0 ? 116 / maxLargeEntryWidth : 1;
    applyScale();
    clipCellEntries();
})()
