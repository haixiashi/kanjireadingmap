// --- Page setup (title, body skeleton, viewport meta, CSS) ---
document.title = '漢字読み方表';
document.body.innerHTML = '<div class="viewport"><table id="grid"><tbody id="tbody"></tbody></table></div>';

// CSS is loaded from src/styles.css and minified by build.py
document.head.innerHTML += '<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"><style>CSS_PLACEHOLDER</style>';

// Arithmetic decoder: rANS base-93 → bytes → bits → kanji/reading data
decodeCell = (() => {
    // --- Base-93 → byte array → stateful bit reader ---
    // Bytes reversed by build.py (pop() reads forward), bits packed LSB-first.
    // sr (shift register) is loaded as byte+256; the sentinel 1-bit at position 8
    // triggers a reload when >>=1 drains it to 1. Past EOF, missing bytes are
    // treated as zero so the arithmetic decoder sees the usual zero-extended tail.
    let byteArr = B(D), sr = 0;
    readBit = () => (sr >>= 1, sr > 1 || (sr = byteArr.pop() | 256), sr & 1);

    // --- 32-bit arithmetic decoder (range coder) ---
    // Uses 32-bit precision with constants TOP=2^31, QUARTER=2^30, MODULUS=2^32.
    // All arithmetic uses % MODULUS to stay in 32-bit unsigned range.
    let RANGE_TOP = 2 ** 31;
    let RANGE_QUARTER = RANGE_TOP / 2;
    let RANGE_MODULUS = RANGE_TOP * 2;
    let rangeLow = 0;
    let rangeHigh = RANGE_MODULUS - 1;
    let rangeValue = 0;
    let codepoint = 19968;          // U+4E00 = 一, first kanji
    let kanjiTable = String.fromCharCode(codepoint);

    // Prime the decoder with 32 bits
    for (let i = 0; i < 32; i++)
        rangeValue = (readBit() + rangeValue * 2) % RANGE_MODULUS;

    // normalize(): shift out resolved bits, read new bits from stream.
    // Called after every decode/decodeUniform to maintain decoder state.
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
            rangeValue = (readBit() + rangeValue * 2) % RANGE_MODULUS;
        }
    };

    // decode(...innerBoundaries): decode one symbol from a 999-scale
    // cumulative frequency model. Pass inner boundary values only;
    // 0 and 999 are implicit. E.g. decode(555) = 2-symbol model with
    // boundary at 555/999. Uses step-based lookup matching encoder.
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

    // decodeUniform(n): decode a uniform symbol in 0..n-1.
    // Uses single-step range subdivision (step = range/n).
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
    // KL-1 deltas using exp-Golomb variant: decode(KD) selects one of 8
    // doubling buckets (q=2,4,8,...,256), then decodeUniform(q) picks
    // the offset within that bucket. First kanji is 一 (U+4E00).
    for (let i = 0; i < KL - 1; i++) {
        let deltaRange = 2 << decode(KD);
        codepoint += decodeUniform(deltaRange) + deltaRange - 1;
        kanjiTable += String.fromCharCode(codepoint);
    }

    // --- Section 2: Kana probability table (82 symbols, k² deltas) ---
    // 81 values decoded via decodeUniform(14), each squared to get the
    // cumulative frequency delta. Builds a 999-scale probability table
    // covering all 82 kana offsets (U+3042–U+3093). The 82nd symbol
    // gets the remainder (999 - sum). Used by decode(...kanaCumFreq)
    // for kana symbol decoding in section 4.
    let kanaCumFreq = [];
    let kanaFreqAcc = 0;
    for (let i = 0; i < 81; i++) {
        let k = decodeUniform(14);
        kanaCumFreq[i] = kanaFreqAcc += k * k;
    }

    // --- Section 3: KN — 45 kana for grid row/col layout ---
    // First kana is always あ (12354 = 0x3042). 44 deltas follow,
    // each decodeUniform(4)+1 giving offsets 1-4.
    let kanaGridCodepoint = 12354;  // あ
    kanaGrid = String.fromCharCode(kanaGridCodepoint);
    for (let i = 0; i < 44; i++)
        kanaGrid += String.fromCharCode(kanaGridCodepoint += decodeUniform(4) + 1);

    // --- Section 4: Cell data (decoded on demand) ---
    // Returns a function that decodes one cell's entries from the stream.
    // cellKana is the 1-2 character kana prefix (row + optional column).
    // Each entry is [kanji, reading, okurigana, isOn].
    return cellKana => {
        // Non-first-column cells: decode cell_present flag (CP model)
        if (cellKana[1] && !decode(CP)) return [];

        let entries = [];
        let switchedToOn = 0;

        for (;;) {
            if (decode(KP)) return entries;

            // Decode one kanji group: 1+ kanji sharing the same reading.
            // First kanji uses KT0 model, subsequent use KT1 (27% more).
            let kanjiGroup = [];
            kanjiGroup.push(kanjiTable[decodeUniform(KL)]);
            while (!decode(K1))
                kanjiGroup.push(kanjiTable[decodeUniform(KL)]);

            // On/kun flag: once a cell switches to on-yomi, remaining groups stay on.
            let isOn = switchedToOn || decode(SW);
            if (isOn) switchedToOn = 1;

            // Variant offsets for dakuten/handakuten readings:
            // d1 = offset for first kana char (conditional on on/kun)
            // d2 = offset for second kana char (conditional on d1)
            let firstKanaVariant = decode(isOn ? DO : DK);
            let secondKanaVariant = (firstKanaVariant ? decode(D1) : decode(D0)) - 1;
            let variantOffsets = [firstKanaVariant, secondKanaVariant];

            // Reconstruct full reading from cell position + katakana shift + variant
            let reading = cellKana.replace(/./g, (c, idx) =>
                String.fromCharCode(c.charCodeAt(0) + isOn * 96 + variantOffsets[idx]));
            let okurigana = '';

            // Extra kana beyond the cell prefix:
            // on-yomi has at most one extra kana, and none in the first column.
            // kun-yomi keeps the general repeat-until-terminator form.
            if (isOn) {
                if (cellKana[1] && decode(OE))
                    reading += String.fromCharCode('&/sD-'.charCodeAt(decode(OM)) + 12416);
            } else {
                while (decode(EF))
                    reading += String.fromCharCode(decode(...kanaCumFreq) + 12354);
                // Okurigana suffix (kun-yomi only, OF flag loop)
                while (decode(OF))
                    okurigana += String.fromCharCode(decode(...kanaCumFreq) + 12354);
            }

            // Emit one entry per kanji in the group (all share reading/okurigana)
            kanjiGroup.map(kanji => entries.push([kanji, reading, okurigana, isOn]));
        }
    };
})();
makeEntrySpan = (kanji, reading, okurigana, isOn) => {
    let span = document.createElement('span');
    span.className = 'kanji-group';
    span.classList.add(isOn ? 'on' : 'kun');
    let rubyEl = document.createElement('ruby');
    rubyEl.textContent = kanji;
    let rtEl = document.createElement('rt');
    rtEl.textContent = reading;
    rubyEl.append(rtEl);
    span.append(rubyEl);
    if (okurigana) span.append(document.createTextNode(okurigana));
    return span;
};

makeHoverEntrySpan = (entry, showReading) => {
    if (showReading) return makeEntrySpan(...entry);
    let [kanji, _reading, okurigana, isOn] = entry;
    let span = document.createElement('span');
    span.className = 'kanji-group';
    span.classList.add(isOn ? 'on' : 'kun');
    span.textContent = kanji + okurigana;
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
    toggleGrid = document.createElement('div');
    toggleGrid.className = 'toggle-grid';
    document.body.append(toggleGrid);

    // --- Reading mode toggle (漢=both / 訓=kun-only / 音=on-only) ---
    readingBtn = document.createElement('button');
    const modes      = ['both', 'kun-only', 'on-only'];
    const modeLabels = '漢訓音';
    modeIdx = modes.indexOf(storage.getItem('rm'));
    if (modeIdx < 0) modeIdx = 0;
    readingBtn.className  = 'fixed-btn reading-toggle';
    readingBtn.textContent = modeLabels[modeIdx];
    if (modes[modeIdx] !== 'both') document.body.classList.add(modes[modeIdx]);
    runViewTransition = update => {
        if (document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches)
            document.startViewTransition(update);
        else
            update();
    };

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
        runViewTransition(() => {
            if (modes[modeIdx] !== 'both') document.body.classList.remove(modes[modeIdx]);
            modeIdx = (modeIdx + 1) % 3;
            if (modes[modeIdx] !== 'both') document.body.classList.add(modes[modeIdx]);
            readingBtn.textContent = modeLabels[modeIdx];
            storage.setItem('rm', modes[modeIdx]);
            updateReadings();
        });
    });
    updateReadings();

    // --- Theme toggle (light / dark) ---
    themeBtn = document.createElement('button');
    themeBtn.className   = 'fixed-btn theme-toggle';
    themeBtn.textContent = '☀';
    if (storage.getItem('dk') === '1') document.body.classList.add('dark');
    if (document.body.classList.contains('dark')) themeBtn.textContent = '☾';
    themeBtn.addEventListener('click', () => {
        let nextDark = !document.body.classList.contains('dark');
        let applyTheme = () => {
            document.body.classList.toggle('dark', nextDark);
            themeBtn.textContent = nextDark ? '☾' : '☀';
            storage.setItem('dk', nextDark ? '1' : '0');
        };
        runViewTransition(applyTheme);
    });

    toggleGrid.append(readingBtn, themeBtn);

    // --- Hover card ---
    hoverCell = null;
    hoverCard = document.createElement('div');
    hoverCard.className = 'hover-card';
    document.body.append(hoverCard);
    hoverCardSize = 0;
    hideHover = () => {
        hoverCell = null;
        hoverCard.classList.remove('visible');
    };

    renderHoverContent = td => {
        hoverCard.innerHTML = '';
        let wm = td.querySelector('.watermark');
        if (wm) hoverCard.append(wm.cloneNode(true));

        const hiddenClass = document.body.classList.contains('kun-only') ? 'on'
                          : document.body.classList.contains('on-only')  ? 'kun' : '';
        let entries = td._entries || [];
        let visible = entries.filter(e => !hiddenClass || (e[3] ? 'on' : 'kun') !== hiddenClass);
        let firstVisible = 1;
        visible.forEach((entry, idx) => {
            let prev = visible[idx - 1];
            let showReading = !entry[3] || !prev || !prev[3] || prev[1] !== entry[1];
            if (!firstVisible && entry[3] && showReading)
                hoverCard.append(document.createElement('br'));
            let span = makeHoverEntrySpan(entry, showReading);
            if (firstVisible) {
                span.classList.add('large');
                firstVisible = 0;
            }
            hoverCard.append(span);
        });
    };

    positionHover = (td, remeasure = 0) => {
        let transformScale = Math.max(scale * 1.2, 0.7);
        let rect  = td.getBoundingClientRect();
        if (remeasure || !hoverCardSize) {
            // cellW is in unscaled CSS px (the card is sized before the transform is applied)
            let cellW = rect.width / scale;
            hoverCard.style.width   = cellW + 'px';
            hoverCard.style.height  = 'auto';

            // Grow toward a square that fits all content
            let scrollH = hoverCard.scrollHeight + 8;
            let side    = Math.sqrt(cellW * scrollH);
            side = Math.max(side, cellW);
            hoverCard.style.width  = side + 'px';
            hoverCard.style.height = 'auto';
            scrollH = hoverCard.scrollHeight + 8;
            hoverCardSize = Math.max(side, scrollH);
            hoverCard.style.width  = hoverCardSize + 'px';
            hoverCard.style.height = hoverCardSize + 'px';
        }

        hoverCard.style.transform = 'scale(' + transformScale + ')';
        let cx = rect.left + rect.width  / 2;
        let cy = rect.top  + rect.height / 2;
        hoverCard.style.left = cx - hoverCardSize / 2 + 'px';
        hoverCard.style.top  = cy - hoverCardSize / 2 + 'px';
    };

    showHover = (td, repositionOnly = 0) => {
        hoverCell = td;
        if (!repositionOnly) renderHoverContent(td);
        positionHover(td, !repositionOnly);
        hoverCard.classList.add('visible');
    };

    // --- Layout constants and state ---
    let fsCap = 1;  // set after table render; used by applyScale
    const TABLE_MARGIN = 320;    // extra space around table for panning headroom
    const MIN_SCALE = 0.5;
    const MAX_SCALE = 2.5;
    const PINCH_HANDOFF_DRAG_THRESHOLD = 8;
    scale = 1;
    settledScale = 1;
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
    table.style.setProperty('--zs', 1);
    tableW = table.offsetWidth;
    tableH = table.offsetHeight;
    resetTimer = 0;
    pendingZoomCleanup = 0;
    computeFontScale = targetScale => targetScale < 1.5 ? Math.min(1.5 / targetScale, fsCap) : 1;

    // --- Scale / zoom ---
    applySettledScale = () => {
        settledScale = scale;
        table.style.setProperty('--zs', settledScale);
        document.body.style.setProperty('--fs', computeFontScale(scale));
        tableW = table.offsetWidth;
        tableH = table.offsetHeight;
        table.style.transform = 'scale(1)';
        let contentW = tableW + TABLE_MARGIN * 2;
        let contentH = tableH + TABLE_MARGIN * 2;
        let wrapW = Math.max(contentW, viewport.clientWidth);
        let wrapH = Math.max(contentH, viewport.clientHeight);
        table.style.left      = (wrapW - tableW) / 2 + 'px';
        table.style.top       = (wrapH - tableH) / 2 + 'px';
        wrapper.style.width   = wrapW + 'px';
        wrapper.style.height  = wrapH + 'px';
    };
    applyTransientTransform = (translateX = 0, translateY = 0) => {
        let transformScale = scale / settledScale;
        table.style.transform =
            (translateX || translateY ? 'translate(' + translateX + 'px,' + translateY + 'px) ' : '') +
            'scale(' + transformScale + ')';
    };

    // Hide spans whose bottom edge is clipped by their .content container.
    // Called once at init and again after each zoom settles.
    clipCellEntries = () => {
        // Batch all writes before all reads to avoid layout thrashing:
        // 1. Reset all visibility (batch write — 1 layout invalidation)
        // 2. Read all offsets (batch read — 1 forced layout, then cached)
        // 3. Apply visibility:hidden + has-more (batch write)
        const allContent = Array.from(document.querySelectorAll('.content'));
        const allSpans = allContent.map(c => Array.from(c.querySelectorAll('.kanji-group')));

        // Batch write: reset all
        allSpans.forEach(spans => spans.forEach(sp => sp.style.visibility = ''));

        // Batch read: measure all (single layout calculation)
        const overflows = allContent.map((content, ci) =>
            allSpans[ci].map(sp => sp.offsetTop + sp.offsetHeight > content.clientHeight)
        );

        // Batch write: apply results
        allContent.forEach((content, ci) => {
            let hiddenCount = 0;
            allSpans[ci].forEach((sp, si) => {
                if (overflows[ci][si]) {
                    sp.style.visibility = 'hidden';
                    hiddenCount++;
                }
            });
            content.parentElement.classList.toggle('has-more', hiddenCount > 0);
            if (hiddenCount) content.parentElement.dataset.more = '+' + hiddenCount;
            else delete content.parentElement.dataset.more;
        });
    };

    // Reset willChange after a zoom gesture to free compositor resources
    resetWillChange = () => {
        zooming = 0;
        pendingZoomCleanup = 0;
        applySettledScale();
        table.style.willChange = 'auto';
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                table.style.willChange = 'transform';
                clipCellEntries();
                if (hoverCell) showHover(hoverCell);
            });
        });
    };
    flushPendingZoomCleanup = () => {
        if (!pendingZoomCleanup) return;
        pendingZoomCleanup = 0;
        table.style.willChange = 'auto';
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                table.style.willChange = 'transform';
                clipCellEntries();
                if (hoverCell) showHover(hoverCell);
            });
        });
    };
    scheduleWillChangeReset = () => {
        clearTimeout(resetTimer);
        resetTimer = setTimeout(resetWillChange, 150);
    };
    settleTransientZoom = deferCleanup => {
        zooming = 0;
        let before = table.getBoundingClientRect();
        applySettledScale();
        let after = table.getBoundingClientRect();
        viewport.scrollLeft += after.left - before.left;
        viewport.scrollTop  += after.top - before.top;
        if (!deferCleanup) return;
        table.style.willChange = 'auto';
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                table.style.willChange = 'transform';
                clipCellEntries();
                if (hoverCell) showHover(hoverCell);
            });
        });
    };
    commitPinch = () => {
        clearTimeout(resetTimer);
        settleTransientZoom(0);
        pendingZoomCleanup = 1;
    };
    applySettledScale();

    // Reposition hover card after scroll/drag, throttled to one rAF per frame
    let hoverPending = 0;
    schedHover = () => {
        if (!zooming && hoverCell && !hoverPending) {
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

    // --- Keyboard pan ---
    document.addEventListener('keydown', e => {
        if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
        let target = e.target;
        if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' ||
            target.tagName === 'SELECT' || target.isContentEditable)) return;

        let step = 128 * scale;
        let dx = 0, dy = 0;
        if (e.key === 'ArrowLeft') dx = -step;
        else if (e.key === 'ArrowRight') dx = step;
        else if (e.key === 'ArrowUp') dy = -step;
        else if (e.key === 'ArrowDown') dy = step;
        else return;

        e.preventDefault();
        viewport.scrollLeft += dx;
        viewport.scrollTop += dy;
        schedHover();
    });

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
        scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale));
        zooming = 1;
        applyTransientTransform();
        let scaleRatio = scale / prevScale;
        viewport.scrollLeft = mouseX * scaleRatio - (e.clientX - rect.left);
        viewport.scrollTop  = mouseY * scaleRatio - (e.clientY - rect.top);
        if (hoverCell) showHover(hoverCell, 1);
        scheduleWillChangeReset();
    }, { passive: false });

    // --- Touch events ---
    gesture = null;
    finalizePinch = () => {
        clearTimeout(resetTimer);
        settleTransientZoom(1);
    };

    viewport.addEventListener('touchstart', e => {
        if (e.touches.length === 2) {
            e.preventDefault();
            cancelAnimationFrame(animFrame);
            velX = velY = 0;
            let a = e.touches[0], b = e.touches[1];
            let cx = (a.clientX + b.clientX) / 2;
            let cy = (a.clientY + b.clientY) / 2;
            let rect = table.getBoundingClientRect();
            gesture = {
                'p': true,
                startDist: Math.hypot(b.clientX - a.clientX, b.clientY - a.clientY),
                startCx: cx,
                startCy: cy,
                cx: cx,
                cy: cy,
                startScale:  scale,
                startScrollX: viewport.scrollLeft,
                startScrollY: viewport.scrollTop,
                anchorX: cx - rect.left,
                anchorY: cy - rect.top,
                translateX: 0,
                translateY: 0,
            };
        } else if (e.touches.length === 1) {
            cancelAnimationFrame(animFrame);
            velX = velY = 0;
            lastTime = performance.now();
            gesture = { 'd': true, x: e.touches[0].clientX, y: e.touches[0].clientY };
        }
    }, { passive: false });

    viewport.addEventListener('touchmove', e => {
        if (e.touches.length === 2 && gesture && gesture['p']) {
            e.preventDefault();
            zooming = 1;
            let a = e.touches[0], b = e.touches[1];
            let dist = Math.hypot(b.clientX - a.clientX, b.clientY - a.clientY);
            let cx   = (a.clientX + b.clientX) / 2;
            let cy   = (a.clientY + b.clientY) / 2;
            scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, gesture.startScale * (dist / gesture.startDist)));
            let transformScale = scale / gesture.startScale;
            gesture.translateX = cx - gesture.startCx - gesture.anchorX * (transformScale - 1);
            gesture.translateY = cy - gesture.startCy - gesture.anchorY * (transformScale - 1);
            applyTransientTransform(gesture.translateX, gesture.translateY);
            gesture.cx = cx; gesture.cy = cy;
            if (hoverCell) showHover(hoverCell, 1);
        } else if (e.touches.length === 1 && gesture && gesture['h']) {
            e.preventDefault();
            let t = e.touches[0];
            let dx = t.clientX - gesture.x;
            let dy = t.clientY - gesture.y;
            if (Math.hypot(dx, dy) >= PINCH_HANDOFF_DRAG_THRESHOLD) {
                lastTime = performance.now();
                velX = velY = 0;
                gesture = { 'd': true, x: t.clientX, y: t.clientY };
            }
        } else if (e.touches.length === 1 && gesture && gesture['d']) {
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
        if (!gesture) return;
        if (gesture['p']) {
            if (e.touches.length === 1) {
                commitPinch();
                let t = e.touches[0];
                gesture = { 'h': true, x: t.clientX, y: t.clientY };
            } else {
                finalizePinch();
                gesture = null;
            }
            return;
        }
        if (gesture['h']) {
            flushPendingZoomCleanup();
            gesture = null;
            return;
        }
        if (gesture['d'] && !e.touches.length) {
            flushPendingZoomCleanup();
            animFrame = requestAnimationFrame(coast);
        }
        gesture = null;
    });

    viewport.addEventListener('touchcancel', () => {
        if (!gesture) return;
        if (gesture['p']) finalizePinch();
        else flushPendingZoomCleanup();
        cancelAnimationFrame(animFrame);
        velX = velY = 0;
        gesture = null;
    });

    // --- Scroll and click ---

    document.addEventListener('click', e => {
        if (e.target.closest('.fixed-btn')) return;
        if (hoverCell && !hoverCell.contains(e.target)) {
            hideHover();
        }
    });

    viewport.addEventListener('click', e => {
        if (didDrag) { didDrag = 0; return; }
        let el = e.target;
        while (el && el.tagName !== 'TD') el = el.parentElement;
        if (!el || !el._entries || !el._entries.length || el.classList.contains('empty')) return;
        if (el === hoverCell) {
            hideHover();
        } else {
            showHover(el);
        }
    });

    // --- Random initial scroll to a non-empty cell ---
    cells     = table.querySelectorAll('td:not(.empty)');
    startCell = cells[Math.random() * cells.length | 0];
    viewport.scrollLeft = (TABLE_MARGIN + startCell.offsetLeft + startCell.offsetWidth  / 2) * scale - viewport.clientWidth  / 2;
    viewport.scrollTop  = (TABLE_MARGIN + startCell.offsetTop  + startCell.offsetHeight / 2) * scale - viewport.clientHeight / 2;
    // Find widest first-entry .large span candidates by estimated pixel width.
    // Ruby width ≈ max(kanji * 26, reading * 11); okurigana ≈ chars * 16.
    // Text length alone misses entries like 柔らかい where short text is wide
    // due to okurigana rendering at the .large base font size (16px).
    let candidates = [];
    document.querySelectorAll('#tbody td:not(.empty)').forEach(td => {
        let entry = td._entries[0];
        if (!entry) return;
        let est = Math.max(entry[0].length * 26, entry[1].length * 11) + entry[2].length * 16;
        let span = td.querySelector('.kanji-group');
        candidates.push([est, span]);
    });
    candidates.sort((a, b) => b[0] - a[0]);
    // Measure top 10 candidates by actual offsetWidth (10 reads = 1 forced layout)
    const probe = document.createElement('div');
    probe.style.cssText = 'position:absolute;left:-9999px;top:-9999px;white-space:nowrap;visibility:hidden';
    document.body.appendChild(probe);
    let maxLargeEntryWidth = 0;
    candidates.slice(0, 10).forEach(c => {
        const clone = c[1].cloneNode(true);
        clone.classList.add('large');
        probe.appendChild(clone);
        maxLargeEntryWidth = Math.max(maxLargeEntryWidth, clone.offsetWidth);
        probe.removeChild(clone);
    });
    document.body.removeChild(probe);
    // 128px cell minus 4px+4px .content insets minus 2px+2px .kanji-group padding = 116px usable
    fsCap = maxLargeEntryWidth > 0 ? 116 / maxLargeEntryWidth : 1;
    applySettledScale();
    clipCellEntries();
})()
