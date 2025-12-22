/* ============================================================================
   PDF Tools — pdf_tools.js
   - Merge (multi-file)
   - Reorder pages (drag/drop tiles + preview)
   - Flatten PDF (via backend)
   ============================================================================ */

(function () {
  function $(sel, root) { return (root || document).querySelector(sel); }
  function el(tag, cls) { const e = document.createElement(tag); if (cls) e.className = cls; return e; }

  function initPdfTools() {
    const shell = document.querySelector('[data-pdf-tool]');
    if (!shell) return;

    const toolId = shell.dataset.toolId || '';
    const implemented = shell.dataset.implemented === '1';
    if (!implemented) return;

    const startUrl = shell.dataset.startUrl || '';

    const dropzone = $('[data-role="dropzone"]', shell);
    const fileInput = $('[data-role="file"]', shell);
    const fileList = $('[data-role="file-list"]', shell);
    const startBtn = $('[data-role="start"]', shell);

    const statusCard = $('[data-role="status-card"]', shell);
    const progressBar = $('[data-role="progress-bar"]', shell);
    const statusText = $('[data-role="status-text"]', shell);
    const expiresText = $('[data-role="expires-text"]', shell);
    const downloadLink = $('[data-role="download"]', shell);

    // Reorder controls
    const reorderWrap = $('[data-role="reorder-wrap"]', shell);
    const pageGrid = $('[data-role="page-grid"]', shell);
    const applyOrderBtn = $('[data-role="apply-order"]', shell);
    const resetOrderBtn = $('[data-role="reset-order"]', shell);
    const pdfPreview = $('[data-role="pdf-preview"]', shell);

    // Merge controls
    const mergeCount = $('[data-role="merge-count"]', shell);
    const mergeWrap = $('[data-role="merge-wrap"]', shell);
    const mergeGrid = $('[data-role="merge-grid"]', shell);
    const applyMergeBtn = $('[data-role="apply-merge"]', shell);
    const resetMergeBtn = $('[data-role="reset-merge"]', shell);

    // Split controls
    const splitWrap = $('[data-role="split-wrap"]', shell);
    const splitMode = $('[data-role="split-mode"]', shell);
    const splitRangeWrap = $('[data-role="split-range-wrap"]', shell);
    const splitRangesInput = $('[data-role="split-ranges"]', shell);
    const splitRangeError = $('[data-role="split-range-error"]', shell);
    const splitRangePreview = $('[data-role="split-range-preview"]', shell);
    const splitRangeGrid = $('[data-role="split-range-grid"]', shell);
    const splitVisualWrap = $('[data-role="split-visual-wrap"]', shell);
    const splitVisualGrid = $('[data-role="split-visual-grid"]', shell);
    const splitVisualCount = $('[data-role="split-visual-count"]', shell);
    const applySplitBtn = $('[data-role="apply-split"]', shell);
    const resetSplitBtn = $('[data-role="reset-split"]', shell);

    // Remove pages controls
    const removeWrap = $('[data-role="remove-wrap"]', shell);
    const removeMode = $('[data-role="remove-mode"]', shell);
    const removeRangeWrap = $('[data-role="remove-range-wrap"]', shell);
    const removeRangesInput = $('[data-role="remove-ranges"]', shell);
    const removeRangeError = $('[data-role="remove-range-error"]', shell);
    const removeVisualWrap = $('[data-role="remove-visual-wrap"]', shell);
    const removeVisualGrid = $('[data-role="remove-visual-grid"]', shell);
    const removeVisualCount = $('[data-role="remove-visual-count"]', shell);
    const applyRemoveBtn = $('[data-role="apply-remove"]', shell);
    const resetRemoveBtn = $('[data-role="reset-remove"]', shell);
    const removePreview = $('[data-role="remove-preview"]', shell);

    // Page numbers controls
    const pageNumbersWrap = $('[data-role="page-numbers-wrap"]', shell);
    const pageNumbersStart = $('[data-role="page-numbers-start"]', shell);
    const pageNumbersEnd = $('[data-role="page-numbers-end"]', shell);
    const pageNumbersFont = $('[data-role="page-numbers-font"]', shell);
    const pageNumbersSize = $('[data-role="page-numbers-size"]', shell);
    const pageNumbersColor = $('[data-role="page-numbers-color"]', shell);
    const pageNumbersError = $('[data-role="page-numbers-error"]', shell);
    const pageNumbersPositionGrid = $('[data-role="page-numbers-position-grid"]', shell);
    const pageNumbersPositionButtons = pageNumbersPositionGrid
      ? Array.from(pageNumbersPositionGrid.querySelectorAll('[data-role="page-numbers-position"]'))
      : [];
    const applyPageNumbersBtn = $('[data-role="apply-page-numbers"]', shell);
    const resetPageNumbersBtn = $('[data-role="reset-page-numbers"]', shell);

    // OCR controls
    const ocrWrap = $('[data-role="ocr-wrap"]', shell);
    const ocrSearch = $('[data-role="ocr-search"]', shell);
    const ocrLangList = $('[data-role="ocr-lang-list"]', shell);
    const ocrLangInputs = ocrLangList
      ? Array.from(ocrLangList.querySelectorAll('[data-role="ocr-lang"]'))
      : [];
    const ocrLangItems = ocrLangList
      ? Array.from(ocrLangList.querySelectorAll('[data-role="ocr-lang-item"]'))
      : [];
    const ocrDpi = $('[data-role="ocr-dpi"]', shell);
    const ocrBinarize = $('[data-role="ocr-binarize"]', shell);
    const ocrError = $('[data-role="ocr-error"]', shell);

    // Image to PDF controls
    const imageWrap = $('[data-role="image-wrap"]', shell);
    const imageGrid = $('[data-role="image-grid"]', shell);
    const applyImageOrderBtn = $('[data-role="apply-image-order"]', shell);
    const resetImageOrderBtn = $('[data-role="reset-image-order"]', shell);
    const imagePreview = $('[data-role="image-preview"]', shell);

    // Compress controls
    const compressMethod = $('[data-role="compress-method"]', shell);
    const compressLevel = $('[data-role="compress-level"]', shell);

    let selectedFiles = [];
    let jobId = null;
    let pollTimer = null;
    let expiresAt = null;
    let expiresTicker = null;

    let pdfBlobUrl = null;
    let resultViewUrl = null;
    let originalOrder = [];
    let draggedTile = null;
    let reorderUiReady = false;

    let mergeUiReady = false;
    let mergeOriginalOrder = [];
    let mergeNameByIdx = new Map();
    let draggedMergeItem = null;

    let splitUiReady = false;
    let splitPageCount = 0;
    let splitThumbsRequested = false;
    let splitThumbsReady = false;
    let splitRanges = [];
    let splitSelectedPages = new Set();

    let removeUiReady = false;
    let removePageCount = 0;
    let removeThumbsRequested = false;
    let removeThumbsReady = false;
    let removeRanges = [];
    let removeSelectedPages = new Set();

    let pageNumbersUiReady = false;
    let pageNumbersPageCount = 0;
    let pageNumbersPosition = 'bottom-right';

    let imageUiReady = false;
    let imageOriginalOrder = [];
    let imageNameByIdx = new Map();
    let draggedImageItem = null;
    let imagePreviewUrl = null;

    function isJobActive() {
      return Boolean(jobId);
    }

    function setStartMode(mode) {
      if (!startBtn) return;
      const next = (mode === 'cancel') ? 'cancel' : 'start';
      startBtn.dataset.mode = next;
      if (next === 'cancel') {
        startBtn.classList.remove('btn-primary');
        startBtn.classList.add('btn-danger');
        startBtn.innerHTML = '<i class="fa-solid fa-xmark" aria-hidden="true"></i> Cancel';
        if (fileInput) fileInput.disabled = true;
        if (dropzone) {
          dropzone.classList.add('is-disabled');
          dropzone.setAttribute('aria-disabled', 'true');
          dropzone.tabIndex = -1;
        }
        if (compressMethod) compressMethod.disabled = true;
        if (compressLevel) compressLevel.disabled = true;
        if (ocrSearch) ocrSearch.disabled = true;
        if (ocrDpi) ocrDpi.disabled = true;
        if (ocrBinarize) ocrBinarize.disabled = true;
        ocrLangInputs.forEach((input) => { input.disabled = true; });
      } else {
        startBtn.classList.add('btn-primary');
        startBtn.classList.remove('btn-danger');
        startBtn.innerHTML = '<i class="fa-solid fa-play" aria-hidden="true"></i> Start';
        if (fileInput) fileInput.disabled = false;
        if (dropzone) {
          dropzone.classList.remove('is-disabled');
          dropzone.removeAttribute('aria-disabled');
          dropzone.tabIndex = 0;
        }
        if (compressMethod) compressMethod.disabled = false;
        if (compressLevel) compressLevel.disabled = false;
        if (ocrSearch) ocrSearch.disabled = false;
        if (ocrDpi) ocrDpi.disabled = false;
        if (ocrBinarize) ocrBinarize.disabled = false;
        ocrLangInputs.forEach((input) => { input.disabled = false; });
      }
      renderFileList();
    }

    function showStatusCard() {
      if (statusCard) statusCard.hidden = false;
    }

    function setProgress(percent) {
      if (!progressBar) return;
      const clamped = Math.max(0, Math.min(100, Number(percent) || 0));
      progressBar.style.width = `${clamped}%`;
    }

    function setStatus(message) {
      if (statusText) statusText.textContent = message || '';
    }

    function setExpires(seconds) {
      if (!expiresText) return;
      if (seconds == null) { expiresText.textContent = ''; return; }
      const s = Math.max(0, Number(seconds) || 0);
      const m = Math.floor(s / 60);
      const r = s % 60;
      expiresText.textContent = `Auto-delete in ${m}:${String(r).padStart(2, '0')}`;
    }

    function stopExpiresTicker() {
      if (expiresTicker) window.clearInterval(expiresTicker);
      expiresTicker = null;
    }

    function startExpiresTicker() {
      stopExpiresTicker();
      if (!expiresAt) return;
      const tick = () => {
        const remaining = Math.max(0, Math.floor((expiresAt - Date.now()) / 1000));
        setExpires(remaining);
        if (remaining <= 0) {
          stopExpiresTicker();
          if (downloadLink) downloadLink.hidden = true;
          setStatus('Job expired.');
        }
      };
      tick();
      expiresTicker = window.setInterval(tick, 1000);
    }

    function stopPolling() {
      if (pollTimer) window.clearTimeout(pollTimer);
      pollTimer = null;
    }

    async function pollStatusOnce() {
      if (!jobId) return;
      let resp;
      try {
        resp = await fetch(`/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/status`, { method: 'GET' });
      } catch (e) {
        setStatus('Connection error while checking status.');
        return;
      }

      if (resp.status === 410) {
        setStatus('Job expired.');
        setProgress(100);
        setExpires(0);
        expiresAt = null;
        stopExpiresTicker();
        stopPolling();
        return;
      }

      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) {
        setStatus(data.msg || `Status failed (HTTP ${resp.status}).`);
        if (resp.status === 404) stopPolling();
        return;
      }

      const state = String(data.state || '');
      const message = data.message || state || 'Working…';
      setStatus(message);
      setProgress(data.percent ?? 0);
      if (data.expires_at) {
        const nextExpires = Number(data.expires_at) * 1000;
        if (!Number.isNaN(nextExpires) && nextExpires !== expiresAt) {
          expiresAt = nextExpires;
          startExpiresTicker();
        }
      } else {
        expiresAt = null;
        stopExpiresTicker();
        setExpires(null);
      }

      if (toolId === 'reorder-pages') {
        const st = String(state).toLowerCase();
        const thumbsReady = Boolean(data.thumbs_ready);
        const pageCount = Number(data.page_count || 0);
        if (!originalOrder.length && pageCount) {
          originalOrder = Array.from({ length: pageCount }, (_, i) => i + 1);
        }
        if (st === 'awaiting_order' && thumbsReady && pageCount && !reorderUiReady) {
          renderReorderThumbs(originalOrder);
          if (reorderWrap) reorderWrap.hidden = false;
          reorderUiReady = true;
          const first = pageGrid?.firstElementChild;
          if (first) first.classList.add('active');
          if (pdfPreview && pdfBlobUrl) {
            pdfPreview.src = `${pdfBlobUrl}#page=1`;
          }
        }

        // After apply-order finishes, show the *reordered* PDF in the preview panel.
        if (st === 'done' && pdfPreview) {
          resultViewUrl = `/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/view?v=${Date.now()}`;
          const tiles = pageGrid ? Array.from(pageGrid.children) : [];
          const active = pageGrid ? pageGrid.querySelector('.active') : null;
          const pageInOrder = active ? Math.max(1, tiles.indexOf(active) + 1) : 1;
          pdfPreview.src = `${resultViewUrl}#page=${encodeURIComponent(String(pageInOrder))}`;
        }
      }

      if (toolId === 'merge') {
        const st = String(state).toLowerCase();
        const thumbsReady = Boolean(data.thumbs_ready);
        const files = Array.isArray(data.merge_files) ? data.merge_files : [];
        if (files.length) {
          mergeNameByIdx = new Map();
          files.forEach((f) => {
            if (!f || typeof f !== 'object') return;
            const idx = Number(f.idx || 0);
            const name = String(f.name || '');
            if (idx > 0 && name) mergeNameByIdx.set(idx, name);
          });
          if (!mergeOriginalOrder.length) {
            mergeOriginalOrder = files.map((f) => Number(f.idx || 0)).filter((n) => n > 0);
          }
        }

        if (st === 'awaiting_order' && thumbsReady && files.length >= 2 && !mergeUiReady) {
          renderMergeThumbs(mergeOriginalOrder);
          if (mergeWrap) mergeWrap.hidden = false;
          mergeUiReady = true;
        }
      }

      if (toolId === 'jpeg-to-pdf') {
        const st = String(state).toLowerCase();
        const thumbsReady = Boolean(data.thumbs_ready);
        const files = Array.isArray(data.image_files) ? data.image_files : [];
        if (files.length) {
          imageNameByIdx = new Map();
          files.forEach((f) => {
            if (!f || typeof f !== 'object') return;
            const idx = Number(f.idx || 0);
            const name = String(f.name || '');
            if (idx > 0 && name) imageNameByIdx.set(idx, name);
          });
          if (!imageOriginalOrder.length) {
            imageOriginalOrder = files.map((f) => Number(f.idx || 0)).filter((n) => n > 0);
          }
        }

        if (st === 'awaiting_order' && thumbsReady && files.length && !imageUiReady) {
          renderImageThumbs(imageOriginalOrder);
          if (imageWrap) imageWrap.hidden = false;
          imageUiReady = true;
          const first = imageGrid?.firstElementChild;
          if (first) first.classList.add('active');
          const firstIdx = first?.dataset?.imageIndex;
          imagePreviewUrl = `/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/image-preview?v=${Date.now()}`;
          if (imagePreview) {
            imagePreview.src = `${imagePreviewUrl}#page=1`;
          }
        }

        if (st === 'done' && imagePreview) {
          resultViewUrl = `/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/view?v=${Date.now()}`;
          const tiles = imageGrid ? Array.from(imageGrid.children) : [];
          const active = imageGrid ? imageGrid.querySelector('.active') : null;
          const pageInOrder = active ? Math.max(1, tiles.indexOf(active) + 1) : 1;
          imagePreview.src = `${resultViewUrl}#page=${encodeURIComponent(String(pageInOrder))}`;
        }
      }

      if (toolId === 'split') {
        const thumbsReady = Boolean(data.thumbs_ready);
        const pageCount = Number(data.page_count || 0);
        if (pageCount) splitPageCount = pageCount;

        if (!splitUiReady) {
          if (splitWrap) splitWrap.hidden = false;
          splitUiReady = true;
          updateSplitModeUi();
        }

        if (thumbsReady !== splitThumbsReady) {
          splitThumbsReady = thumbsReady;
          updateSplitActionState();
          if (splitThumbsReady) {
            if (splitMode?.value === 'visual' && splitVisualGrid && splitVisualGrid.children.length === 0) {
              renderSplitVisualGrid(splitPageCount);
            }
            if (splitMode?.value === 'ranges' && splitRanges.length) {
              renderSplitRangePreview(splitRanges);
              if (splitRangePreview) splitRangePreview.hidden = false;
            }
          }
        }
      }

      if (toolId === 'remove-pages') {
        const thumbsReady = Boolean(data.thumbs_ready);
        const pageCount = Number(data.page_count || 0);
        if (pageCount) removePageCount = pageCount;

        if (!removeUiReady) {
          if (removeWrap) removeWrap.hidden = false;
          removeUiReady = true;
          updateRemoveModeUi();
        }

        if (thumbsReady !== removeThumbsReady) {
          removeThumbsReady = thumbsReady;
          updateRemoveActionState();
          if (removeThumbsReady && removeMode?.value === 'visual' && removeVisualGrid && removeVisualGrid.children.length === 0) {
            renderRemoveVisualGrid(removePageCount);
          }
        }

        if (String(state).toLowerCase() === 'done' && removePreview) {
          resultViewUrl = `/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/view?v=${Date.now()}`;
          removePreview.src = `${resultViewUrl}#page=1`;
        }
      }

      if (toolId === 'page-numbers') {
        const pageCount = Number(data.page_count || 0);
        if (pageCount) pageNumbersPageCount = pageCount;
        if (!pageNumbersUiReady) {
          if (pageNumbersWrap) pageNumbersWrap.hidden = false;
          pageNumbersUiReady = true;
          initPageNumbersDefaults();
        }
      }

      if (downloadLink) {
        if (String(state).toLowerCase() === 'done') {
          downloadLink.hidden = false;
          downloadLink.href = `/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/download`;
        } else {
          downloadLink.hidden = true;
          downloadLink.href = '#';
        }
      }

      const doneStates = new Set(['done', 'error', 'canceled']);
      if (doneStates.has(String(state).toLowerCase())) {
        stopPolling();
      }
    }

    function startPolling() {
      stopPolling();
      const tick = async () => {
        await pollStatusOnce();
        if (!pollTimer) return;
        pollTimer = window.setTimeout(tick, 800);
      };
      pollTimer = window.setTimeout(tick, 0);
    }

    function renderFileList() {
      if (mergeCount) {
        if (!selectedFiles.length) {
          mergeCount.textContent = '';
          mergeCount.hidden = true;
        } else {
          const n = selectedFiles.length;
          mergeCount.textContent = `${n} PDF${n === 1 ? '' : 's'} selected.`;
          mergeCount.hidden = false;
        }
      }

      if (!fileList) return;
      fileList.innerHTML = '';
      if (toolId === 'jpeg-to-pdf') {
        if (!selectedFiles.length) {
          fileList.textContent = 'No images selected.';
        } else {
          const n = selectedFiles.length;
          fileList.textContent = `${n} image${n === 1 ? '' : 's'} selected.`;
        }
        return;
      }
      if (!selectedFiles.length) {
        fileList.textContent = 'No files selected.';
        return;
      }

      selectedFiles.forEach((file, idx) => {
        const row = el('div', 'result-item');
        const name = el('div');
        name.textContent = file.name;
        const meta = el('span', 'badge');
        meta.textContent = `${(file.size / 1024).toFixed(1)} KB`;
        const rm = el('button', 'btn-ghost');
        rm.type = 'button';
        rm.textContent = '✕';
        rm.style.padding = '4px 8px';
        rm.style.marginLeft = 'auto';
        if (isJobActive()) {
          rm.disabled = true;
          rm.title = 'Cancel the current job to change files.';
        } else {
          rm.addEventListener('click', () => {
            selectedFiles.splice(idx, 1);
            if (toolId === 'reorder-pages' || toolId === 'flatten' || toolId === 'split' || toolId === 'compress' || toolId === 'remove-pages' || toolId === 'page-numbers' || toolId === 'ocr') {
              // single-file tools
              selectedFiles = [];
              if (fileInput) fileInput.value = '';
            }
            resetReorderUi();
            resetMergeUi();
            resetSplitUi();
            resetRemoveUi();
            resetPageNumbersUi();
            renderFileList();
          });
        }
        row.append(name, meta, rm);
        fileList.append(row);
      });
    }

    function resetReorderUi() {
      if (toolId !== 'reorder-pages') return;
      if (reorderWrap) reorderWrap.hidden = true;
      if (pageGrid) pageGrid.innerHTML = '';
      originalOrder = [];
      draggedTile = null;
      reorderUiReady = false;
      resultViewUrl = null;
      if (pdfPreview) pdfPreview.src = '';
      if (pdfBlobUrl) {
        URL.revokeObjectURL(pdfBlobUrl);
        pdfBlobUrl = null;
      }
    }

    function resetMergeUi() {
      if (toolId !== 'merge') return;
      if (mergeWrap) mergeWrap.hidden = true;
      if (mergeGrid) mergeGrid.innerHTML = '';
      mergeUiReady = false;
      mergeOriginalOrder = [];
      mergeNameByIdx = new Map();
      draggedMergeItem = null;
    }

    function resetSplitUi() {
      if (toolId !== 'split') return;
      if (splitWrap) splitWrap.hidden = true;
      if (splitRangeGrid) splitRangeGrid.innerHTML = '';
      if (splitVisualGrid) splitVisualGrid.innerHTML = '';
      if (splitRangesInput) splitRangesInput.value = '';
      if (splitRangeError) splitRangeError.textContent = '';
      if (splitRangePreview) splitRangePreview.hidden = true;
      if (splitVisualWrap) splitVisualWrap.hidden = true;
      if (splitRangeWrap) splitRangeWrap.hidden = false;
      if (splitVisualCount) splitVisualCount.textContent = '';
      splitUiReady = false;
      splitPageCount = 0;
      splitThumbsRequested = false;
      splitThumbsReady = false;
      splitRanges = [];
      splitSelectedPages = new Set();
      if (applySplitBtn) applySplitBtn.disabled = true;
    }

    function resetRemoveUi() {
      if (toolId !== 'remove-pages') return;
      if (removeWrap) removeWrap.hidden = true;
      if (removeRangeWrap) removeRangeWrap.hidden = false;
      if (removeRangesInput) removeRangesInput.value = '';
      if (removeRangeError) removeRangeError.textContent = '';
      if (removeVisualGrid) removeVisualGrid.innerHTML = '';
      if (removeVisualWrap) removeVisualWrap.hidden = true;
      if (removeVisualCount) removeVisualCount.textContent = '';
      if (removePreview) removePreview.src = '';
      removeUiReady = false;
      removePageCount = 0;
      removeThumbsRequested = false;
      removeThumbsReady = false;
      removeRanges = [];
      removeSelectedPages = new Set();
      if (applyRemoveBtn) applyRemoveBtn.disabled = true;
      resultViewUrl = null;
    }

    function parseSplitRanges(raw, totalPages) {
      const text = String(raw || '').trim();
      if (!text) {
        return { ranges: [], error: '' };
      }

      const cleaned = text.replace(/\bto\b/gi, '-');
      const parts = cleaned.split(',').map((part) => part.trim()).filter(Boolean);
      const ranges = [];

      for (const part of parts) {
        if (/^\d+$/.test(part)) {
          const value = Number(part);
          if (!Number.isFinite(value) || value < 1) {
            return { ranges: [], error: 'Page numbers must be 1 or higher.' };
          }
          if (totalPages && value > totalPages) {
            return { ranges: [], error: `Page ${value} exceeds page count (${totalPages}).` };
          }
          ranges.push({ start: value, end: value });
          continue;
        }

        const match = part.match(/^(\d+)\s*-\s*(\d+)$/);
        if (!match) {
          return { ranges: [], error: 'Use ranges like 1-3, 5-7.' };
        }
        const start = Number(match[1]);
        const end = Number(match[2]);
        if (!Number.isFinite(start) || !Number.isFinite(end) || start < 1 || end < 1) {
          return { ranges: [], error: 'Page numbers must be 1 or higher.' };
        }
        if (start > end) {
          return { ranges: [], error: 'Ranges must be in ascending order (start <= end).' };
        }
        if (totalPages && end > totalPages) {
          return { ranges: [], error: `Range exceeds page count (${totalPages}).` };
        }
        ranges.push({ start, end });
      }

      return { ranges, error: '' };
    }

    async function ensureSplitThumbs() {
      if (toolId !== 'split' || !jobId || splitThumbsRequested || splitThumbsReady) return;
      splitThumbsRequested = true;
      try {
        const resp = await fetch(`/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/split-thumbs`, { method: 'POST' });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data.ok) {
          splitThumbsRequested = false;
          setStatus(data.msg || `Preview failed (HTTP ${resp.status}).`);
        }
      } catch (e) {
        splitThumbsRequested = false;
        setStatus('Connection error while generating previews.');
      }
    }

    function renderSplitRangePreview(ranges) {
      if (!splitRangeGrid) return;
      splitRangeGrid.innerHTML = '';
      ranges.forEach((range, idx) => {
        const wrap = el('div', 'pdf-range-item');
        const tile = el('div', 'pdf-thumb-tile');
        const img = el('img', 'pdf-thumb-img');
        const num = el('div', 'pdf-thumb-num');
        img.alt = `Range ${idx + 1}: ${range.start}-${range.end}`;
        img.loading = 'lazy';
        img.draggable = false;
        img.src = `/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/thumb/${encodeURIComponent(String(range.start))}`;
        num.textContent = String(range.start);
        tile.append(img, num);
        const label = el('div', 'pdf-range-label');
        label.textContent = `${range.start}-${range.end}`;
        wrap.append(tile, label);
        splitRangeGrid.append(wrap);
      });
    }

    function renderSplitVisualGrid(totalPages) {
      if (!splitVisualGrid) return;
      splitVisualGrid.innerHTML = '';
      splitSelectedPages = new Set();
      for (let page = 1; page <= totalPages; page += 1) {
        const tile = el('div', 'pdf-thumb-tile');
        tile.dataset.pageNumber = String(page);
        const img = el('img', 'pdf-thumb-img');
        img.alt = `Page ${page}`;
        img.loading = 'lazy';
        img.draggable = false;
        img.src = `/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/thumb/${encodeURIComponent(String(page))}`;
        const num = el('div', 'pdf-thumb-num');
        num.textContent = String(page);
        tile.append(img, num);

        tile.addEventListener('click', () => {
          const pageNumber = Number(tile.dataset.pageNumber || 0);
          if (!pageNumber) return;
          if (splitSelectedPages.has(pageNumber)) {
            splitSelectedPages.delete(pageNumber);
            tile.classList.remove('is-selected');
          } else {
            splitSelectedPages.add(pageNumber);
            tile.classList.add('is-selected');
          }
          updateSplitActionState();
          updateSplitVisualCount();
        });

        splitVisualGrid.append(tile);
      }
      updateSplitVisualCount();
    }

    function updateSplitVisualCount() {
      if (!splitVisualCount) return;
      const count = splitSelectedPages.size;
      splitVisualCount.textContent = count ? `${count} page${count === 1 ? '' : 's'} selected.` : '';
    }

    function updateSplitActionState() {
      if (!applySplitBtn) return;
      if (!jobId) {
        applySplitBtn.disabled = true;
        return;
      }
      const mode = String(splitMode?.value || 'ranges');
      if (mode === 'ranges') {
        applySplitBtn.disabled = !(splitRanges.length > 0 && splitThumbsReady);
        return;
      }
      if (mode === 'visual') {
        applySplitBtn.disabled = !(splitThumbsReady && splitSelectedPages.size > 0);
        return;
      }
      applySplitBtn.disabled = false;
    }

    function updateSplitModeUi() {
      if (toolId !== 'split') return;
      const mode = String(splitMode?.value || 'ranges');
      if (splitRangeWrap) splitRangeWrap.hidden = mode !== 'ranges';
      if (splitRangePreview) splitRangePreview.hidden = true;
      if (splitVisualWrap) splitVisualWrap.hidden = mode !== 'visual';
      if (splitRangeError) splitRangeError.textContent = '';
      if (mode === 'visual') {
        ensureSplitThumbs();
      } else if (mode === 'ranges') {
        if (splitRanges.length) ensureSplitThumbs();
      }
      updateSplitActionState();
    }

    function updateSplitRanges() {
      if (toolId !== 'split') return;
      const total = splitPageCount || 0;
      const { ranges, error } = parseSplitRanges(splitRangesInput?.value || '', total);
      splitRanges = ranges;
      if (splitRangeError) splitRangeError.textContent = error || '';
      if (!ranges.length || error) {
        if (splitRangePreview) splitRangePreview.hidden = true;
        updateSplitActionState();
        return;
      }
      ensureSplitThumbs();
      if (splitThumbsReady) {
        renderSplitRangePreview(ranges);
        if (splitRangePreview) splitRangePreview.hidden = false;
      } else if (splitRangePreview) {
        splitRangePreview.hidden = true;
      }
      updateSplitActionState();
    }

    async function ensureRemoveThumbs() {
      if (toolId !== 'remove-pages' || !jobId || removeThumbsRequested || removeThumbsReady) return;
      removeThumbsRequested = true;
      try {
        const resp = await fetch(`/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/remove-thumbs`, { method: 'POST' });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data.ok) {
          removeThumbsRequested = false;
          setStatus(data.msg || `Preview failed (HTTP ${resp.status}).`);
        }
      } catch (e) {
        removeThumbsRequested = false;
        setStatus('Connection error while generating previews.');
      }
    }

    function renderRemoveVisualGrid(totalPages) {
      if (!removeVisualGrid) return;
      removeVisualGrid.innerHTML = '';
      removeSelectedPages = new Set();
      for (let page = 1; page <= totalPages; page += 1) {
        const tile = el('div', 'pdf-thumb-tile');
        tile.dataset.pageNumber = String(page);

        const img = el('img', 'pdf-thumb-img');
        img.alt = `Page ${page}`;
        img.loading = 'lazy';
        img.draggable = false;
        img.src = `/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/thumb/${encodeURIComponent(String(page))}`;

        const num = el('div', 'pdf-thumb-num');
        num.textContent = String(page);

        tile.append(img, num);
        tile.addEventListener('click', () => {
          const pageNumber = Number(tile.dataset.pageNumber || 0);
          if (!pageNumber) return;
          if (removeSelectedPages.has(pageNumber)) {
            removeSelectedPages.delete(pageNumber);
            tile.classList.remove('is-removed');
          } else {
            removeSelectedPages.add(pageNumber);
            tile.classList.add('is-removed');
          }
          invalidateRemovePreview();
          updateRemoveActionState();
          updateRemoveVisualCount();
        });

        removeVisualGrid.append(tile);
      }
      updateRemoveVisualCount();
    }

    function updateRemoveVisualCount() {
      if (!removeVisualCount) return;
      const count = removeSelectedPages.size;
      removeVisualCount.textContent = count ? `${count} page${count === 1 ? '' : 's'} marked for removal.` : '';
    }

    function updateRemoveActionState() {
      if (!applyRemoveBtn) return;
      if (!jobId) {
        applyRemoveBtn.disabled = true;
        return;
      }
      const mode = String(removeMode?.value || 'ranges');
      if (mode === 'ranges') {
        const hasError = Boolean(removeRangeError?.textContent);
        applyRemoveBtn.disabled = !(removeRanges.length > 0 && !hasError);
        return;
      }
      if (mode === 'visual') {
        applyRemoveBtn.disabled = !(removeThumbsReady && removeSelectedPages.size > 0);
        return;
      }
      applyRemoveBtn.disabled = false;
    }

    function invalidateRemovePreview() {
      if (toolId !== 'remove-pages') return;
      resultViewUrl = null;
      if (removePreview) removePreview.src = '';
    }

    function updateRemoveModeUi() {
      if (toolId !== 'remove-pages') return;
      const mode = String(removeMode?.value || 'ranges');
      if (removeRangeWrap) removeRangeWrap.hidden = mode !== 'ranges';
      if (removeVisualWrap) removeVisualWrap.hidden = mode !== 'visual';
      if (removeRangeError) removeRangeError.textContent = '';
      if (mode === 'visual') {
        ensureRemoveThumbs();
      }
      updateRemoveActionState();
      invalidateRemovePreview();
    }

    function updateRemoveRanges() {
      if (toolId !== 'remove-pages') return;
      const total = removePageCount || 0;
      const { ranges, error } = parseSplitRanges(removeRangesInput?.value || '', total);
      removeRanges = ranges;
      if (removeRangeError) removeRangeError.textContent = error || '';
      updateRemoveActionState();
      invalidateRemovePreview();
    }

    function parsePageNumberRange() {
      const total = pageNumbersPageCount || 0;
      const startRaw = pageNumbersStart?.value;
      const endRaw = pageNumbersEnd?.value;
      const start = Number(startRaw);
      const end = Number(endRaw);

      if (!Number.isFinite(start) || start < 1) {
        return { start: 0, end: 0, error: 'Start page must be 1 or higher.' };
      }
      if (!Number.isFinite(end) || end < 1) {
        return { start: 0, end: 0, error: 'End page must be 1 or higher.' };
      }
      if (start > end) {
        return { start: 0, end: 0, error: 'Start page must be before end page.' };
      }
      if (total && end > total) {
        return { start: 0, end: 0, error: `End page exceeds page count (${total}).` };
      }
      return { start, end, error: '' };
    }

    function updatePageNumbersPreviewStyle() {
      if (!pageNumbersPositionButtons.length) return;
      const color = pageNumbersColor?.value || '#111111';
      const sizeRaw = Number(pageNumbersSize?.value || 12);
      const size = Math.max(8, Math.min(22, sizeRaw));
      const font = String(pageNumbersFont?.value || 'Helvetica');
      const fontFamily = {
        Helvetica: '"Helvetica", Arial, sans-serif',
        'Helvetica-Bold': '"Helvetica", Arial, sans-serif',
        'Times-Roman': '"Times New Roman", Times, serif',
        'Times-Bold': '"Times New Roman", Times, serif',
        Courier: '"Courier New", Courier, monospace',
      }[font] || '"Helvetica", Arial, sans-serif';

      pageNumbersPositionButtons.forEach((btn) => {
        const num = btn.querySelector('.pdf-position-num');
        if (!num) return;
        num.style.color = color;
        num.style.fontSize = `${size}px`;
        num.style.fontFamily = fontFamily;
        num.style.fontWeight = font.includes('Bold') ? '700' : '600';
      });
    }

    function updatePageNumbersThumbs() {
      if (toolId !== 'page-numbers' || !jobId) return;
      const startValue = Number(pageNumbersStart?.value || 0);
      const valid = Number.isFinite(startValue) && startValue >= 1
        && (!pageNumbersPageCount || startValue <= pageNumbersPageCount);

      pageNumbersPositionButtons.forEach((btn) => {
        const img = btn.querySelector('.pdf-position-thumb');
        const num = btn.querySelector('.pdf-position-num');
        if (num && valid) num.textContent = String(startValue);
        if (!img) return;
        if (!valid) {
          img.removeAttribute('src');
          img.hidden = true;
          return;
        }
        img.hidden = false;
        img.loading = 'lazy';
        img.alt = `Page ${startValue}`;
        img.src = `/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/thumb/${encodeURIComponent(String(startValue))}`;
      });
    }

    function updatePageNumbersActionState() {
      if (!applyPageNumbersBtn) return;
      if (!jobId) {
        applyPageNumbersBtn.disabled = true;
        return;
      }
      const { error } = parsePageNumberRange();
      if (pageNumbersError) pageNumbersError.textContent = error || '';
      applyPageNumbersBtn.disabled = Boolean(error);
    }

    function updatePageNumbersPosition(selected) {
      pageNumbersPosition = selected || pageNumbersPosition || 'bottom-right';
      pageNumbersPositionButtons.forEach((btn) => {
        const match = btn.dataset.position === pageNumbersPosition;
        btn.classList.toggle('is-selected', match);
      });
    }

    function resetPageNumbersUi() {
      if (toolId !== 'page-numbers') return;
      if (pageNumbersWrap) pageNumbersWrap.hidden = true;
      pageNumbersUiReady = false;
      pageNumbersPageCount = 0;
      pageNumbersPosition = 'bottom-right';
      if (pageNumbersStart) pageNumbersStart.value = '';
      if (pageNumbersEnd) pageNumbersEnd.value = '';
      if (pageNumbersError) pageNumbersError.textContent = '';
      updatePageNumbersPosition(pageNumbersPosition);
      updatePageNumbersPreviewStyle();
      updatePageNumbersActionState();
    }

    function setOcrError(message) {
      if (!ocrError) return;
      ocrError.textContent = message || '';
    }

    function getSelectedOcrLanguages() {
      return ocrLangInputs.filter((input) => input.checked).map((input) => input.value);
    }

    function filterOcrLanguages() {
      if (!ocrLangItems.length) return;
      const query = String(ocrSearch?.value || '').trim().toLowerCase();
      ocrLangItems.forEach((item) => {
        const text = (item.dataset.lang || item.textContent || '').toLowerCase();
        const matches = !query || text.includes(query);
        item.hidden = !matches;
        item.style.display = matches ? '' : 'none';
      });
    }

    function resetImageUi() {
      if (toolId !== 'jpeg-to-pdf') return;
      if (imageWrap) imageWrap.hidden = true;
      if (imageGrid) imageGrid.innerHTML = '';
      if (imagePreview) imagePreview.src = '';
      imageUiReady = false;
      imageOriginalOrder = [];
      imageNameByIdx = new Map();
      draggedImageItem = null;
      resultViewUrl = null;
      imagePreviewUrl = null;
    }

    function initPageNumbersDefaults() {
      if (pageNumbersStart) {
        pageNumbersStart.min = '1';
        pageNumbersStart.max = pageNumbersPageCount ? String(pageNumbersPageCount) : '';
        pageNumbersStart.value = '1';
      }
      if (pageNumbersEnd) {
        pageNumbersEnd.min = '1';
        pageNumbersEnd.max = pageNumbersPageCount ? String(pageNumbersPageCount) : '';
        pageNumbersEnd.value = pageNumbersPageCount ? String(pageNumbersPageCount) : '';
      }
      updatePageNumbersPosition(pageNumbersPosition || 'bottom-right');
      updatePageNumbersPreviewStyle();
      updatePageNumbersActionState();
      updatePageNumbersThumbs();
    }

    function setSelectedFiles(files, { mergeIntoExisting = false } = {}) {
      if (isJobActive()) return;
      const incoming = Array.from(files || []);
      if (!incoming.length) return;

      if ((toolId === 'merge' || toolId === 'jpeg-to-pdf') && mergeIntoExisting) {
        selectedFiles = selectedFiles.concat(incoming);
      } else {
        selectedFiles = (toolId === 'merge' || toolId === 'jpeg-to-pdf') ? incoming : [incoming[0]];
      }

      if (toolId === 'reorder-pages') {
        const f = selectedFiles[0];
        if (pdfBlobUrl) URL.revokeObjectURL(pdfBlobUrl);
        pdfBlobUrl = f ? URL.createObjectURL(f) : null;
        resultViewUrl = null;
        if (pdfPreview && pdfBlobUrl) pdfPreview.src = `${pdfBlobUrl}#page=1`;
        if (reorderWrap) reorderWrap.hidden = true;
        if (pageGrid) pageGrid.innerHTML = '';
        reorderUiReady = false;
      }

      if (toolId === 'merge') {
        resetMergeUi();
      }
      if (toolId === 'split') {
        resetSplitUi();
      }
      if (toolId === 'remove-pages') {
        resetRemoveUi();
      }
      if (toolId === 'page-numbers') {
        resetPageNumbersUi();
      }
      if (toolId === 'jpeg-to-pdf') {
        resetImageUi();
      }

      renderFileList();
    }

    function bindDropzone() {
      if (!dropzone || !fileInput) return;

      const choose = () => fileInput.click();
      dropzone.addEventListener('click', choose);
      dropzone.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          choose();
        }
      });
      fileInput.addEventListener('change', () => {
        setSelectedFiles(fileInput.files, { mergeIntoExisting: toolId === 'merge' || toolId === 'jpeg-to-pdf' });
        fileInput.value = '';
      });

      dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        if (isJobActive()) return;
        dropzone.classList.add('dragover');
      });
      dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
      dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('dragover');
        if (isJobActive()) return;
        setSelectedFiles(e.dataTransfer?.files, { mergeIntoExisting: toolId === 'merge' || toolId === 'jpeg-to-pdf' });
      });
    }

    function renderReorderThumbs(order) {
      if (!pageGrid) return;
      pageGrid.innerHTML = '';

      order.forEach((n) => {
        const tile = el('div', 'pdf-thumb-tile');
        tile.dataset.pageNumber = String(n);
        tile.draggable = true;

        const img = el('img', 'pdf-thumb-img');
        img.alt = `Page ${n}`;
        img.loading = 'lazy';
        img.draggable = false;
        img.src = `/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/thumb/${encodeURIComponent(String(n))}`;

        const num = el('div', 'pdf-thumb-num');
        num.textContent = String(n);

        tile.append(img, num);

        tile.addEventListener('click', () => {
          Array.from(pageGrid.children).forEach((c) => c.classList.remove('active'));
          tile.classList.add('active');
          const tiles = Array.from(pageGrid.children);
          const pageIndex = tiles.indexOf(tile);
          if (pdfPreview) {
            if (resultViewUrl) {
              const pageInOrder = Math.max(1, pageIndex + 1);
              pdfPreview.src = `${resultViewUrl}#page=${encodeURIComponent(String(pageInOrder))}`;
            } else if (pdfBlobUrl) {
              pdfPreview.src = `${pdfBlobUrl}#page=${encodeURIComponent(String(n))}`;
            }
          }
        });

        tile.addEventListener('dragstart', (e) => {
          draggedTile = tile;
          tile.classList.add('dragging');
          e.dataTransfer.effectAllowed = 'move';
        });

        tile.addEventListener('dragend', () => {
          tile.classList.remove('dragging');
          draggedTile = null;
        });

        tile.addEventListener('dragover', (e) => {
          e.preventDefault();
          e.dataTransfer.dropEffect = 'move';
        });

        tile.addEventListener('drop', (e) => {
          e.preventDefault();
          const target = tile;
          if (!draggedTile || draggedTile === target) return;

          const tiles = Array.from(pageGrid.children);
          const from = tiles.indexOf(draggedTile);
          const to = tiles.indexOf(target);
          if (from < 0 || to < 0) return;

          if (from < to) {
            pageGrid.insertBefore(draggedTile, target.nextSibling);
          } else {
            pageGrid.insertBefore(draggedTile, target);
          }

          // If a finished (reordered) preview is showing, it's now outdated because the order changed.
          if (resultViewUrl) {
            resultViewUrl = null;
            const active = pageGrid.querySelector('.active');
            const originalPage = active?.dataset?.pageNumber;
            if (pdfPreview && pdfBlobUrl && originalPage) {
              pdfPreview.src = `${pdfBlobUrl}#page=${encodeURIComponent(String(originalPage))}`;
            }
          }
        });

        pageGrid.append(tile);
      });
    }

    function currentOrderFromDom() {
      if (!pageGrid) return [];
      return Array.from(pageGrid.children).map((c) => Number(c.dataset.pageNumber || 0)).filter(Boolean);
    }

    function updateMergeBadges() {
      if (!mergeGrid) return;
      Array.from(mergeGrid.children).forEach((node, idx) => {
        const badge = node.querySelector('.pdf-thumb-num');
        if (badge) badge.textContent = String(idx + 1);
      });
    }

    function renderMergeThumbs(order) {
      if (!mergeGrid) return;
      mergeGrid.innerHTML = '';

      order.forEach((idx, pos) => {
        const wrap = el('div', 'pdf-merge-item');
        wrap.dataset.fileIndex = String(idx);
        wrap.draggable = true;

        const tile = el('div', 'pdf-thumb-tile');

        const img = el('img', 'pdf-thumb-img');
        const name = mergeNameByIdx.get(Number(idx)) || '';
        img.alt = name ? `Preview: ${name}` : `Document ${pos + 1}`;
        img.loading = 'lazy';
        img.draggable = false;
        img.src = `/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/merge-thumb/${encodeURIComponent(String(idx))}`;

        const num = el('div', 'pdf-thumb-num');
        num.textContent = String(pos + 1);

        tile.append(img, num);

        const label = el('div', 'pdf-merge-name');
        label.textContent = name;

        wrap.append(tile, label);

        wrap.addEventListener('dragstart', (e) => {
          draggedMergeItem = wrap;
          wrap.classList.add('dragging');
          e.dataTransfer.effectAllowed = 'move';
        });

        wrap.addEventListener('dragend', () => {
          wrap.classList.remove('dragging');
          draggedMergeItem = null;
        });

        wrap.addEventListener('dragover', (e) => {
          e.preventDefault();
          e.dataTransfer.dropEffect = 'move';
        });

        wrap.addEventListener('drop', (e) => {
          e.preventDefault();
          const target = wrap;
          if (!draggedMergeItem || draggedMergeItem === target) return;

          const items = Array.from(mergeGrid.children);
          const from = items.indexOf(draggedMergeItem);
          const to = items.indexOf(target);
          if (from < 0 || to < 0) return;

          if (from < to) {
            mergeGrid.insertBefore(draggedMergeItem, target.nextSibling);
          } else {
            mergeGrid.insertBefore(draggedMergeItem, target);
          }
          updateMergeBadges();
        });

        mergeGrid.append(wrap);
      });
    }

    function currentMergeOrderFromDom() {
      if (!mergeGrid) return [];
      return Array.from(mergeGrid.children).map((c) => Number(c.dataset.fileIndex || 0)).filter(Boolean);
    }

    function renderImageThumbs(order) {
      if (!imageGrid) return;
      imageGrid.innerHTML = '';

      order.forEach((idx, pos) => {
        const tile = el('div', 'pdf-thumb-tile');
        tile.dataset.imageIndex = String(idx);
        tile.draggable = true;

        const img = el('img', 'pdf-thumb-img');
        const name = imageNameByIdx.get(Number(idx)) || '';
        img.alt = name ? `Image: ${name}` : `Image ${pos + 1}`;
        img.loading = 'lazy';
        img.draggable = false;
        img.src = `/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/image-thumb/${encodeURIComponent(String(idx))}`;

        const num = el('div', 'pdf-thumb-num');
        num.textContent = String(pos + 1);

        tile.append(img, num);

        tile.addEventListener('click', () => {
          Array.from(imageGrid.children).forEach((c) => c.classList.remove('active'));
          tile.classList.add('active');
          const tiles = Array.from(imageGrid.children);
          const pageIndex = tiles.indexOf(tile);
          if (!imagePreview) return;
          if (resultViewUrl) {
            const pageInOrder = Math.max(1, pageIndex + 1);
            imagePreview.src = `${resultViewUrl}#page=${encodeURIComponent(String(pageInOrder))}`;
            return;
          }
          if (imagePreviewUrl) {
            const originalIndex = imageOriginalOrder.indexOf(idx);
            const pageInDefault = originalIndex >= 0 ? originalIndex + 1 : Math.max(1, pageIndex + 1);
            imagePreview.src = `${imagePreviewUrl}#page=${encodeURIComponent(String(pageInDefault))}`;
            return;
          }
          imagePreview.src = `/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/image/${encodeURIComponent(String(idx))}`;
        });

        tile.addEventListener('dragstart', (e) => {
          draggedImageItem = tile;
          tile.classList.add('dragging');
          e.dataTransfer.effectAllowed = 'move';
        });

        tile.addEventListener('dragend', () => {
          tile.classList.remove('dragging');
          draggedImageItem = null;
        });

        tile.addEventListener('dragover', (e) => {
          e.preventDefault();
          e.dataTransfer.dropEffect = 'move';
        });

        tile.addEventListener('drop', (e) => {
          e.preventDefault();
          const target = tile;
          if (!draggedImageItem || draggedImageItem === target) return;

          const items = Array.from(imageGrid.children);
          const from = items.indexOf(draggedImageItem);
          const to = items.indexOf(target);
          if (from < 0 || to < 0) return;

          if (from < to) {
            imageGrid.insertBefore(draggedImageItem, target.nextSibling);
          } else {
            imageGrid.insertBefore(draggedImageItem, target);
          }
          Array.from(imageGrid.children).forEach((node, index) => {
            const badge = node.querySelector('.pdf-thumb-num');
            if (badge) badge.textContent = String(index + 1);
          });

          if (resultViewUrl) {
            resultViewUrl = null;
            if (imagePreview) imagePreview.src = '';
          }
        });

        imageGrid.append(tile);
      });
    }

    function currentImageOrderFromDom() {
      if (!imageGrid) return [];
      return Array.from(imageGrid.children).map((c) => Number(c.dataset.imageIndex || 0)).filter(Boolean);
    }

    async function startJob() {
      if (!startBtn) return;
      if (isJobActive()) return;

      stopPolling();
      jobId = null;
      resultViewUrl = null;
      if (downloadLink) downloadLink.hidden = true;
      showStatusCard();
      setProgress(0);
      setExpires(null);
      setStatus('Uploading…');
      expiresAt = null;
      stopExpiresTicker();

      startBtn.disabled = true;
      try {
        if (toolId === 'merge') {
          if (selectedFiles.length < 2) throw new Error('Select at least two PDFs to merge.');
          const fd = new FormData();
          selectedFiles.forEach((f) => fd.append('file', f, f.name));
          const resp = await fetch(startUrl, { method: 'POST', body: fd });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || !data.ok) throw new Error(data.msg || `HTTP ${resp.status}`);
          jobId = data.job_id;
          mergeUiReady = false;
          mergeOriginalOrder = [];
          mergeNameByIdx = new Map();
          draggedMergeItem = null;
          if (mergeWrap) mergeWrap.hidden = true;
          if (mergeGrid) mergeGrid.innerHTML = '';
          setStartMode('cancel');
          setStatus('Generating document previews…');
          setProgress(0);
          startPolling();
          return;
        }

        if (toolId === 'reorder-pages') {
          const file = selectedFiles[0];
          if (!file) throw new Error('Select a PDF to reorder.');
          const fd = new FormData();
          fd.append('file', file, file.name);
          const resp = await fetch(startUrl, { method: 'POST', body: fd });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || !data.ok) throw new Error(data.msg || `HTTP ${resp.status}`);
          jobId = data.job_id;
          const pageCount = Number(data.page_count || 0);
          if (!pageCount) throw new Error('Unable to determine page count.');

          originalOrder = Array.from({ length: pageCount }, (_, i) => i + 1);
          reorderUiReady = false;
          if (reorderWrap) reorderWrap.hidden = true;
          if (pageGrid) pageGrid.innerHTML = '';
          setStartMode('cancel');
          setStatus('Generating page previews…');
          setProgress(0);
          startPolling();
          return;
        }

        if (toolId === 'split') {
          const file = selectedFiles[0];
          if (!file) throw new Error('Select a PDF to split.');
          const fd = new FormData();
          fd.append('file', file, file.name);
          const resp = await fetch(startUrl, { method: 'POST', body: fd });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || !data.ok) throw new Error(data.msg || `HTTP ${resp.status}`);
          jobId = data.job_id;
          splitPageCount = Number(data.page_count || 0);
          splitUiReady = false;
          splitThumbsRequested = false;
          splitThumbsReady = false;
          splitRanges = [];
          splitSelectedPages = new Set();
          if (splitRangeGrid) splitRangeGrid.innerHTML = '';
          if (splitVisualGrid) splitVisualGrid.innerHTML = '';
          if (splitRangePreview) splitRangePreview.hidden = true;
          if (splitRangesInput) splitRangesInput.value = '';
          if (splitRangeError) splitRangeError.textContent = '';
          if (splitWrap) splitWrap.hidden = false;
          if (splitVisualCount) splitVisualCount.textContent = '';
          setStartMode('cancel');
          setStatus('Ready to split.');
          setProgress(0);
          startPolling();
          updateSplitModeUi();
          updateSplitActionState();
          return;
        }

        if (toolId === 'remove-pages') {
          const file = selectedFiles[0];
          if (!file) throw new Error('Select a PDF to edit.');
          const fd = new FormData();
          fd.append('file', file, file.name);
          const resp = await fetch(startUrl, { method: 'POST', body: fd });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || !data.ok) throw new Error(data.msg || `HTTP ${resp.status}`);
          jobId = data.job_id;
          removePageCount = Number(data.page_count || 0);
          removeUiReady = false;
          removeThumbsRequested = false;
          removeThumbsReady = false;
          removeRanges = [];
          removeSelectedPages = new Set();
          if (removeRangesInput) removeRangesInput.value = '';
          if (removeRangeError) removeRangeError.textContent = '';
          if (removeVisualGrid) removeVisualGrid.innerHTML = '';
          if (removeVisualWrap) removeVisualWrap.hidden = true;
          if (removeWrap) removeWrap.hidden = false;
          if (removeVisualCount) removeVisualCount.textContent = '';
          if (removePreview) removePreview.src = '';
          resultViewUrl = null;
          setStartMode('cancel');
          setStatus('Ready to remove pages.');
          setProgress(0);
          startPolling();
          updateRemoveModeUi();
          updateRemoveActionState();
          return;
        }

        if (toolId === 'page-numbers') {
          const file = selectedFiles[0];
          if (!file) throw new Error('Select a PDF to edit.');
          const fd = new FormData();
          fd.append('file', file, file.name);
          const resp = await fetch(startUrl, { method: 'POST', body: fd });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || !data.ok) throw new Error(data.msg || `HTTP ${resp.status}`);
          jobId = data.job_id;
          pageNumbersPageCount = Number(data.page_count || 0);
          pageNumbersUiReady = true;
          if (pageNumbersWrap) pageNumbersWrap.hidden = false;
          if (pageNumbersError) pageNumbersError.textContent = '';
          setStartMode('cancel');
          setStatus('Ready to add page numbers.');
          setProgress(0);
          startPolling();
          initPageNumbersDefaults();
          return;
        }

        if (toolId === 'ocr') {
          setOcrError('');
          const file = selectedFiles[0];
          if (!file) throw new Error('Select a PDF to OCR.');
          const languages = getSelectedOcrLanguages();
          if (!languages.length) {
            setOcrError('Select at least one OCR language.');
            throw new Error('Select at least one OCR language.');
          }
          const dpiValue = Number(ocrDpi?.value || 288);
          if (!Number.isFinite(dpiValue) || ![192, 288, 384].includes(dpiValue)) {
            setOcrError('DPI must be one of: 192, 288, 384.');
            throw new Error('DPI must be one of: 192, 288, 384.');
          }
          const fd = new FormData();
          fd.append('file', file, file.name);
          languages.forEach((lang) => fd.append('ocr_language', lang));
          fd.append('ocr_dpi', String(Math.round(dpiValue)));
          if (ocrBinarize?.checked) fd.append('ocr_binarize', '1');
          const resp = await fetch(startUrl, { method: 'POST', body: fd });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || !data.ok) throw new Error(data.msg || `HTTP ${resp.status}`);
          jobId = data.job_id;
          setStartMode('cancel');
          setStatus('Running OCR…');
          setProgress(0);
          startPolling();
          return;
        }

        if (toolId === 'jpeg-to-pdf') {
          if (!selectedFiles.length) throw new Error('Select at least one image.');
          const fd = new FormData();
          selectedFiles.forEach((f) => fd.append('file', f, f.name));
          const resp = await fetch(startUrl, { method: 'POST', body: fd });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || !data.ok) throw new Error(data.msg || `HTTP ${resp.status}`);
          jobId = data.job_id;
          imageUiReady = false;
          imageOriginalOrder = [];
          imageNameByIdx = new Map();
          draggedImageItem = null;
          if (imageWrap) imageWrap.hidden = true;
          if (imageGrid) imageGrid.innerHTML = '';
          if (imagePreview) imagePreview.src = '';
          resultViewUrl = null;
          setStartMode('cancel');
          setStatus('Generating image previews…');
          setProgress(0);
          startPolling();
          return;
        }

        if (toolId === 'compress') {
          const file = selectedFiles[0];
          if (!file) throw new Error('Select a PDF to compress.');
          const method = compressMethod?.value || 'photon';
          const level = compressLevel?.value || 'medium';
          const fd = new FormData();
          fd.append('file', file, file.name);
          fd.append('compression_method', method);
          fd.append('compression_level', level);
          const resp = await fetch(startUrl, { method: 'POST', body: fd });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || !data.ok) throw new Error(data.msg || `HTTP ${resp.status}`);
          jobId = data.job_id;
          setStartMode('cancel');
          setStatus('Compressing PDF…');
          setProgress(0);
          startPolling();
          return;
        }

        if (toolId === 'flatten') {
          const file = selectedFiles[0];
          if (!file) throw new Error('Select a PDF to flatten.');
          const fd = new FormData();
          fd.append('file', file, file.name);
          const resp = await fetch(startUrl, { method: 'POST', body: fd });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || !data.ok) throw new Error(data.msg || `HTTP ${resp.status}`);
          jobId = data.job_id;
          setStartMode('cancel');
          startPolling();
          return;
        }

        throw new Error('This tool is not wired yet.');
      } catch (err) {
        setProgress(100);
        setExpires(null);
        setStatus(err?.message || String(err));
      } finally {
        startBtn.disabled = false;
      }
    }

    async function cancelJob() {
      if (!startBtn) return;
      const currentJob = jobId;
      stopPolling();
      jobId = null;
      resultViewUrl = null;
      if (downloadLink) {
        downloadLink.hidden = true;
        downloadLink.href = '#';
      }
      if (statusCard) statusCard.hidden = true;
      setProgress(0);
      setExpires(null);
      setStatus('');
      expiresAt = null;
      stopExpiresTicker();

      resetReorderUi();
      resetMergeUi();
      resetSplitUi();
      resetRemoveUi();
      resetPageNumbersUi();
      resetImageUi();
      selectedFiles = [];
      if (fileInput) fileInput.value = '';

      setStartMode('start');

      if (currentJob) {
        try {
          await fetch(`/api/pdf-tools/jobs/${encodeURIComponent(currentJob)}/cancel`, { method: 'POST' });
        } catch (e) {
          // ignore; UI already reset
        }
      }
    }

    async function applyOrder() {
      if (toolId !== 'reorder-pages') return;
      if (!jobId) { setStatus('Upload a PDF first.'); return; }
      const order = currentOrderFromDom();
      if (!order.length) { setStatus('No pages found.'); return; }

      // A new output is being generated; keep preview on the original PDF until done.
      resultViewUrl = null;
      showStatusCard();
      setStatus('Submitting order…');
      setProgress(10);

      const resp = await fetch(`/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/apply-order`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ order }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) {
        setProgress(100);
        setStatus(data.msg || `HTTP ${resp.status}`);
        return;
      }
      setStatus('Reordering pages…');
      setProgress(25);
      startPolling();
    }

    async function applyMerge() {
      if (toolId !== 'merge') return;
      if (!jobId) { setStatus('Upload PDFs first.'); return; }
      const order = currentMergeOrderFromDom();
      if (order.length < 2) { setStatus('Select at least two PDFs.'); return; }

      showStatusCard();
      setStatus('Submitting order…');
      setProgress(10);

      const resp = await fetch(`/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/apply-merge`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ order }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) {
        setProgress(100);
        setStatus(data.msg || `HTTP ${resp.status}`);
        return;
      }

      setStatus('Merging PDFs…');
      setProgress(25);
      startPolling();
    }

    async function applySplit() {
      if (toolId !== 'split') return;
      if (!jobId) { setStatus('Upload a PDF first.'); return; }
      const mode = String(splitMode?.value || 'ranges');
      const payload = { mode };

      if (mode === 'ranges') {
        if (!splitRanges.length) { setStatus('Enter at least one valid range.'); return; }
        if (!splitThumbsReady) { setStatus('Generating previews…'); return; }
        payload.ranges = splitRanges;
      } else if (mode === 'visual') {
        if (!splitThumbsReady) { setStatus('Generating previews…'); return; }
        if (!splitSelectedPages.size) { setStatus('Select at least one page.'); return; }
        payload.pages = Array.from(splitSelectedPages).sort((a, b) => a - b);
      }

      showStatusCard();
      setStatus('Submitting split…');
      setProgress(10);

      const resp = await fetch(`/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/apply-split`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) {
        setProgress(100);
        setStatus(data.msg || `HTTP ${resp.status}`);
        return;
      }

      setStatus('Splitting PDF…');
      setProgress(25);
      startPolling();
    }

    async function applyRemove() {
      if (toolId !== 'remove-pages') return;
      if (!jobId) { setStatus('Upload a PDF first.'); return; }
      const mode = String(removeMode?.value || 'ranges');
      const payload = { mode };

      if (mode === 'ranges') {
        if (!removeRanges.length) { setStatus('Enter at least one valid range.'); return; }
        payload.ranges = removeRanges;
      } else if (mode === 'visual') {
        if (!removeThumbsReady) { setStatus('Generating previews…'); return; }
        if (!removeSelectedPages.size) { setStatus('Select at least one page to remove.'); return; }
        payload.pages = Array.from(removeSelectedPages).sort((a, b) => a - b);
      }

      invalidateRemovePreview();
      showStatusCard();
      setStatus('Submitting removal…');
      setProgress(10);

      const resp = await fetch(`/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/apply-remove`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) {
        setProgress(100);
        setStatus(data.msg || `HTTP ${resp.status}`);
        return;
      }

      setStatus('Removing pages…');
      setProgress(25);
      startPolling();
    }

    async function applyPageNumbers() {
      if (toolId !== 'page-numbers') return;
      if (!jobId) { setStatus('Upload a PDF first.'); return; }
      const { start, end, error } = parsePageNumberRange();
      if (error) {
        setStatus(error);
        updatePageNumbersActionState();
        return;
      }
      const payload = {
        start_page: start,
        end_page: end,
        position: pageNumbersPosition || 'bottom-right',
        font_name: pageNumbersFont?.value || 'Helvetica',
        font_size: Number(pageNumbersSize?.value || 12),
        font_color: pageNumbersColor?.value || '#111111',
      };

      showStatusCard();
      setStatus('Submitting page numbers…');
      setProgress(10);

      const resp = await fetch(`/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/apply-page-numbers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) {
        setProgress(100);
        setStatus(data.msg || `HTTP ${resp.status}`);
        return;
      }

      setStatus('Adding page numbers…');
      setProgress(25);
      startPolling();
    }

    async function applyImageOrder() {
      if (toolId !== 'jpeg-to-pdf') return;
      if (!jobId) { setStatus('Upload images first.'); return; }
      const order = currentImageOrderFromDom();
      if (!order.length) { setStatus('No images found.'); return; }

      resultViewUrl = null;
      showStatusCard();
      setStatus('Submitting order…');
      setProgress(10);

      const resp = await fetch(`/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/apply-images`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ order }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) {
        setProgress(100);
        setStatus(data.msg || `HTTP ${resp.status}`);
        return;
      }

      setStatus('Converting images…');
      setProgress(25);
      startPolling();
    }

    function resetOrder() {
      if (toolId !== 'reorder-pages') return;
      if (!originalOrder.length) return;
      resultViewUrl = null;
      renderReorderThumbs(originalOrder);
      if (pdfPreview && pdfBlobUrl) pdfPreview.src = `${pdfBlobUrl}#page=1`;
    }

    function resetMerge() {
      if (toolId !== 'merge') return;
      if (!mergeOriginalOrder.length) return;
      renderMergeThumbs(mergeOriginalOrder);
    }

    function resetSplit() {
      if (toolId !== 'split') return;
      splitRanges = [];
      splitSelectedPages = new Set();
      if (splitRangesInput) splitRangesInput.value = '';
      if (splitRangeError) splitRangeError.textContent = '';
      if (splitRangeGrid) splitRangeGrid.innerHTML = '';
      if (splitRangePreview) splitRangePreview.hidden = true;
      if (splitVisualGrid) {
        Array.from(splitVisualGrid.children).forEach((tile) => tile.classList.remove('is-selected'));
      }
      updateSplitVisualCount();
      updateSplitActionState();
    }

    function resetRemove() {
      if (toolId !== 'remove-pages') return;
      removeRanges = [];
      removeSelectedPages = new Set();
      if (removeRangesInput) removeRangesInput.value = '';
      if (removeRangeError) removeRangeError.textContent = '';
      if (removeVisualGrid) {
        Array.from(removeVisualGrid.children).forEach((tile) => tile.classList.remove('is-removed'));
      }
      updateRemoveVisualCount();
      updateRemoveActionState();
      invalidateRemovePreview();
    }

    function resetPageNumbers() {
      if (toolId !== 'page-numbers') return;
      pageNumbersPosition = 'bottom-right';
      if (pageNumbersStart) pageNumbersStart.value = '1';
      if (pageNumbersEnd) pageNumbersEnd.value = pageNumbersPageCount ? String(pageNumbersPageCount) : '';
      if (pageNumbersFont) pageNumbersFont.value = 'Helvetica';
      if (pageNumbersSize) pageNumbersSize.value = '12';
      if (pageNumbersColor) pageNumbersColor.value = '#111111';
      if (pageNumbersError) pageNumbersError.textContent = '';
      updatePageNumbersPosition(pageNumbersPosition);
      updatePageNumbersPreviewStyle();
      updatePageNumbersActionState();
      updatePageNumbersThumbs();
    }

    function resetImageOrder() {
      if (toolId !== 'jpeg-to-pdf') return;
      if (!imageOriginalOrder.length) return;
      resultViewUrl = null;
      renderImageThumbs(imageOriginalOrder);
      const first = imageGrid?.firstElementChild;
      if (first) first.classList.add('active');
      if (imagePreview) {
        if (imagePreviewUrl) {
          imagePreview.src = `${imagePreviewUrl}#page=1`;
        } else {
          const firstIdx = first?.dataset?.imageIndex;
          if (firstIdx) {
            imagePreview.src = `/api/pdf-tools/jobs/${encodeURIComponent(jobId)}/image/${encodeURIComponent(String(firstIdx))}`;
          } else {
            imagePreview.src = '';
          }
        }
      }
    }

    bindDropzone();

    setStartMode('start');

    startBtn?.addEventListener('click', async () => {
      if (!startBtn || startBtn.disabled) return;
      const mode = startBtn.dataset.mode || 'start';
      if (mode === 'cancel') {
        await cancelJob();
      } else {
        await startJob();
      }
    });
    applyOrderBtn?.addEventListener('click', applyOrder);
    resetOrderBtn?.addEventListener('click', resetOrder);
    applyMergeBtn?.addEventListener('click', applyMerge);
    resetMergeBtn?.addEventListener('click', resetMerge);
    applySplitBtn?.addEventListener('click', applySplit);
    resetSplitBtn?.addEventListener('click', resetSplit);
    applyRemoveBtn?.addEventListener('click', applyRemove);
    resetRemoveBtn?.addEventListener('click', resetRemove);
    applyPageNumbersBtn?.addEventListener('click', applyPageNumbers);
    resetPageNumbersBtn?.addEventListener('click', resetPageNumbers);
    applyImageOrderBtn?.addEventListener('click', applyImageOrder);
    resetImageOrderBtn?.addEventListener('click', resetImageOrder);

    splitMode?.addEventListener('change', () => {
      updateSplitModeUi();
      if (splitMode?.value === 'visual' && splitThumbsReady && splitVisualGrid?.children.length === 0) {
        renderSplitVisualGrid(splitPageCount || 0);
      }
      if (splitMode?.value === 'ranges') {
        updateSplitRanges();
      }
    });
    splitRangesInput?.addEventListener('input', () => {
      updateSplitRanges();
    });

    removeMode?.addEventListener('change', () => {
      updateRemoveModeUi();
      if (removeMode?.value === 'visual' && removeThumbsReady && removeVisualGrid?.children.length === 0) {
        renderRemoveVisualGrid(removePageCount || 0);
      }
      if (removeMode?.value === 'ranges') {
        updateRemoveRanges();
      }
    });
    removeRangesInput?.addEventListener('input', () => {
      updateRemoveRanges();
    });

    pageNumbersStart?.addEventListener('input', () => {
      updatePageNumbersActionState();
      updatePageNumbersThumbs();
    });
    pageNumbersEnd?.addEventListener('input', () => {
      updatePageNumbersActionState();
    });
    pageNumbersFont?.addEventListener('change', () => {
      updatePageNumbersPreviewStyle();
    });
    pageNumbersSize?.addEventListener('input', () => {
      updatePageNumbersPreviewStyle();
      updatePageNumbersActionState();
    });
    pageNumbersColor?.addEventListener('input', () => {
      updatePageNumbersPreviewStyle();
    });
    pageNumbersPositionButtons.forEach((btn) => {
      btn.addEventListener('click', () => {
        updatePageNumbersPosition(btn.dataset.position || 'bottom-right');
      });
    });

    ocrSearch?.addEventListener('input', () => {
      filterOcrLanguages();
    });
    ocrLangInputs.forEach((input) => {
      input.addEventListener('change', () => {
        setOcrError('');
      });
    });
    ocrDpi?.addEventListener('input', () => {
      setOcrError('');
    });

    filterOcrLanguages();
    renderFileList();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPdfTools);
  } else {
    initPdfTools();
  }
})();
