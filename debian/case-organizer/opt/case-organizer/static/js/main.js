/* ============================================================================
   Case Organizer — main.js (full rewrite, v2)
   ============================================================================
   Covers:
   - Global helpers ($, el)
   - Taxonomies (SUBCATS, CASE_TYPES)
   - Search (basic + advanced) with single authoritative renderResults
   - Infinite year dropdown
   - Create Case form
   - Manage Case form (year/month/case, domain→subcategory, file upload)
   - Note.json button (either Add OR View/Edit) + modal wiring
   - Flash auto-dismiss
   - Theme toggle
   ============================================================================ */

// Small helpers
function $(sel){ return document.querySelector(sel); }
function el(tag, cls){ const e=document.createElement(tag); if(cls) e.className=cls; return e; }

const HTML_ESCAPE = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
function escapeHtml(value){
  return String(value ?? '').replace(/[&<>\"']/g, ch => HTML_ESCAPE[ch] || ch);
}

const CASEORG_STATE = window.CaseOrg || {};
const CASEORG_IS_ADMIN = Boolean(CASEORG_STATE.isAdmin);

// --- Data: subcategories & case types ----------------------------------
const SUBCATS = {
  Criminal: [
    "Anticipatory Bail","Appeals","Bail","Charges","Criminal Miscellaneous",
    "Orders/Judgments","Primary Documents","Revisions","Trial","Writs",
    "Reference","Transfer Petitions"
  ],
  Civil: [
    "Civil Main","Civil Miscellaneous Main","Civil Appeal","Civil Revision",
    "Civil Writ Petition","Orders/Judgments","Primary Documents",
    "Reference","Transfer Petitions"
  ],
  Commercial: [
    "Civil Main","Civil Miscellaneous Main","Civil Appeal","Civil Revision",
    "Civil Writ Petition","Orders/Judgments","Primary Documents",
    "Reference","Transfer Petitions"
  ],
  // NOTE: Intentionally no "Case Law" key so subcategory is disabled when selected.
};

const CASE_TYPES = {
  Criminal: [
    "498A (Cruelty/Dowry)","Murder","Rape","Sexual Harassment","Hurt",
    "138 NI Act","Fraud","Human Trafficking","NDPS","PMLA","POCSO","Constitutional","Others"
  ],
  Civil: [
    "Property","Rent Control","Inheritance/Succession","Contract",
    "Marital Divorce","Marital Maintenance","Marital Guardianship","Constitutional","Others"
  ],
  Commercial: [
    "Trademark","Copyright","Patent","Banking","Others"
  ],
};

const NOTE_TEMPLATE_DEFAULT = `{
  "Petitioner Name": "",
  "Petitioner Address": "",
  "Petitioner Contact": "",

  "Respondent Name": "",
  "Respondent Address": "",
  "Respondent Contact": "",

  "Our Party": "",

  "Case Category": "",
  "Case Subcategory": "",
  "Case Type": "",

  "Court of Origin": {
    "State": "",
    "District": "",
    "Court/Forum": ""
  },

  "Current Court/Forum": {
    "State": "",
    "District": "",
    "Court/Forum": ""
  },

  "Additional Notes": ""
}`;

function defaultNoteTemplate(){
  return NOTE_TEMPLATE_DEFAULT;
}

// ------------------ Common UI utilities ------------------
function populateOptions(select, arr, placeholder="Select"){
  if (!select) return;
  select.innerHTML = "";
  const opt = el("option");
  opt.value = "";
  opt.textContent = placeholder;
  select.append(opt);
  arr.forEach(v => {
    const o = el("option");
    o.textContent = v;
    select.append(o);
  });
  select.disabled = false;
}

function openNotesModal(content, intent = 'update', context = null){
  if (typeof window._openNotesWith === 'function') {
    window._openNotesWith(content || '', intent || 'update', context || null);
    return;
  }
  const modal = document.getElementById('notesModal');
  const editor = document.getElementById('notesEditor');
  if (!modal || !editor) return;
  editor.value = content || '';
  editor.style.display = 'block';
  modal.removeAttribute('hidden');
  modal.setAttribute('aria-hidden','false');
}

// --- Search helpers -----------------------------------------------------
async function runBasicSearch(){
  const q = ($('#search-q')?.value || '').trim();
  const url = new URL('/search', location.origin);
  if (q) url.searchParams.set('q', q);
  const r = await fetch(url);
  const data = await r.json().catch(()=>({results:[]}));
  renderResults(data.results || []);
}

async function runAdvancedSearch(){
  const params = new URLSearchParams();
  const party = (document.getElementById('party')?.value || '').trim();
  const year  = (document.getElementById('year')?.value || '').trim();   // hidden #year (from year-dd)
  const month = document.getElementById('month')?.value || '';
  const domain = document.getElementById('adv-domain')?.value || '';
  const subcat = document.getElementById('adv-subcat')?.value || '';

  if (party) params.set('party', party);
  if (year)  params.set('year', year);
  if (month) params.set('month', month);
  if (domain) params.set('domain', domain);
  if (subcat) params.set('subcategory', subcat);

  // Only include 'type' if the element still exists (back-compat)
  const typeEl = document.getElementById('type');
  if (typeEl && typeEl.value) params.set('type', typeEl.value);

  const r = await fetch(`/search?${params.toString()}`);
  const data = await r.json().catch(()=>({results:[]}));
  renderResults(data.results || []);
}

// ------------ Infinite, scrollable year dropdown (virtualized-ish) ------------
function initYearDropdown(wrapperId, hiddenInputId, startYear = 2025) {
  const wrap = document.getElementById(wrapperId);
  if (!wrap) return;
  const trigger = wrap.querySelector('.yd-trigger');
  const panel = wrap.querySelector('.yd-panel');
  const hidden = document.getElementById(hiddenInputId);
  if (!trigger || !panel || !hidden) return;

  // Config
  const CHUNK = 80;          // how many years to render per side at once
  const THRESHOLD = 40;      // when to grow (px from top/bottom)
  const itemHeight = 32;     // keep in sync with CSS

  // State
  let anchor = startYear;    // visual center
  let from = anchor - CHUNK; // inclusive
  let to   = anchor + CHUNK; // inclusive
  let selected = startYear;

  // Ensure initial value
  hidden.value = String(selected);
  trigger.textContent = `Year: ${selected}`;

  // Utilities
  function render(initial = false) {
    const frag = document.createDocumentFragment();
    for (let y = from; y <= to; y++) {
      const opt = document.createElement('div');
      opt.className = 'yd-item';
      opt.setAttribute('role','option');
      opt.dataset.year = String(y);
      opt.textContent = String(y);
      if (y === selected) opt.classList.add('selected');
      frag.appendChild(opt);
    }
    if (initial) {
      panel.innerHTML = '';
    }
    panel.appendChild(frag);

    if (initial) {
      // scroll so that "anchor" sits roughly in the middle
      const midIndex = anchor - from;
      panel.scrollTop = Math.max(0, midIndex * itemHeight - panel.clientHeight/2 + itemHeight/2);
    }
  }

  function open() {
    if (!panel.hasAttribute('hidden')) return;
    panel.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');

    // First open: initial render
    if (!panel.dataset.ready) {
      render(true);
      panel.dataset.ready = '1';
    }
    // focus panel for keyboard nav
    panel.focus({ preventScroll: true });
  }

  function close() {
    if (panel.hasAttribute('hidden')) return;
    panel.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
  }

  function setYear(y) {
    selected = y;
    hidden.value = String(y);
    trigger.textContent = `Year: ${y}`;
    // update selection highlight
    panel.querySelectorAll('.yd-item.selected').forEach(n => n.classList.remove('selected'));
    const elx = panel.querySelector(`.yd-item[data-year="${y}"]`);
    if (elx) elx.classList.add('selected');
  }

  // Expand list when scrolling near top/bottom
  panel.addEventListener('scroll', () => {
    const nearTop = panel.scrollTop <= THRESHOLD;
    const nearBottom = (panel.scrollHeight - panel.clientHeight - panel.scrollTop) <= THRESHOLD;

    if (nearTop) {
      // prepend older years
      const oldFrom = from;
      from = from - CHUNK;
      const frag = document.createDocumentFragment();
      for (let y = from; y < oldFrom; y++) {
        const opt = document.createElement('div');
        opt.className = 'yd-item';
        opt.setAttribute('role','option');
        opt.dataset.year = String(y);
        opt.textContent = String(y);
        if (y === selected) opt.classList.add('selected');
        frag.appendChild(opt);
      }
      panel.prepend(frag);
      // maintain visual position
      panel.scrollTop += CHUNK * itemHeight;
    }

    if (nearBottom) {
      const oldTo = to;
      to = to + CHUNK;
      const frag = document.createDocumentFragment();
      for (let y = oldTo + 1; y <= to; y++) {
        const opt = document.createElement('div');
        opt.className = 'yd-item';
        opt.setAttribute('role','option');
        opt.dataset.year = String(y);
        opt.textContent = String(y);
        if (y === selected) opt.classList.add('selected');
        frag.appendChild(opt);
      }
      panel.append(frag);
    }
  });

  // Click select
  panel.addEventListener('click', (e) => {
    const d = e.target.closest('.yd-item');
    if (!d) return;
    const y = parseInt(d.dataset.year, 10);
    if (!isNaN(y)) {
      setYear(y);
      close();
    }
  });

  // Keyboard on panel (Up/Down/Page/Home/End/Enter/Esc)
  panel.tabIndex = 0;
  panel.addEventListener('keydown', (e) => {
    const cur = parseInt(hidden.value || String(selected), 10);
    if (!['ArrowUp','ArrowDown','PageUp','PageDown','Home','End','Enter','Escape'].includes(e.key)) return;
    e.preventDefault();
    let next = cur;
    if (e.key === 'ArrowUp') next = cur + 1;
    if (e.key === 'ArrowDown') next = cur - 1;
    if (e.key === 'PageUp') next = cur + 10;
    if (e.key === 'PageDown') next = cur - 10;
    if (e.key === 'Home') next = 9999;
    if (e.key === 'End') next = 1;
    if (e.key === 'Enter' || e.key === 'Escape') { close(); return; }

    setYear(next);

    // Ensure year element exists; extend if necessary
    if (next < from + 5) {
      const oldFrom = from;
      from = next - CHUNK;
      const frag = document.createDocumentFragment();
      for (let y = from; y < oldFrom; y++) {
        const opt = document.createElement('div');
        opt.className = 'yd-item';
        opt.setAttribute('role','option');
        opt.dataset.year = String(y);
        opt.textContent = String(y);
        if (y === selected) opt.classList.add('selected');
        frag.appendChild(opt);
      }
      panel.prepend(frag);
      panel.scrollTop += (oldFrom - from) * itemHeight;
    } else if (next > to - 5) {
      const oldTo = to;
      to = next + CHUNK;
      const frag = document.createDocumentFragment();
      for (let y = oldTo + 1; y <= to; y++) {
        const opt = document.createElement('div');
        opt.className = 'yd-item';
        opt.setAttribute('role','option');
        opt.dataset.year = String(y);
        opt.textContent = String(y);
        if (y === selected) opt.classList.add('selected');
        frag.appendChild(opt);
      }
      panel.append(frag);
    }

    // Scroll selected into view
    const elx = panel.querySelector(`.yd-item[data-year="${next}"]`);
    if (elx) {
      const r = elx.getBoundingClientRect();
      const pr = panel.getBoundingClientRect();
      if (r.top < pr.top + 4) panel.scrollTop -= (pr.top + 4 - r.top);
      if (r.bottom > pr.bottom - 4) panel.scrollTop += (r.bottom - (pr.bottom - 4));
    }
  });

  // Open/close trigger + wheel fine-tune
  trigger.addEventListener('click', () => (panel.hidden ? open() : close()));
  trigger.addEventListener('wheel', (e) => {
    if (!panel.hidden) return;
    if (!e.ctrlKey) {
      e.preventDefault();
      const delta = e.deltaY < 0 ? +1 : -1;
      setYear(selected + delta);
    }
  }, { passive: false });

  // Close when clicking outside
  document.addEventListener('click', (e) => {
    if (!wrap.contains(e.target)) close();
  });

  // Initial text label already set
}

// ------------- Results renderer (authoritative) ----------------
function openConfirm(message) {
  return new Promise((resolve) => {
    const modal = document.getElementById('confirmModal');
    const text  = document.getElementById('confirmText');
    const yes   = document.getElementById('confirmYes');
    const no    = document.getElementById('confirmNo');
    const x     = document.getElementById('confirmClose');

    if (!modal || !yes || !no || !x) {
      const ok = window.confirm(message || 'Do you want to delete this file?');
      resolve(ok);
      return;
    }

    if (text) text.textContent = message || 'Do you want to delete this file?';
    modal.removeAttribute('hidden');
    modal.setAttribute('aria-hidden', 'false');

    const cleanup = () => {
      modal.setAttribute('hidden', '');
      modal.setAttribute('aria-hidden', 'true');
      yes.removeEventListener('click', onYes);
      no.removeEventListener('click', onNo);
      x.removeEventListener('click', onNo);
    };
    const onYes = () => { cleanup(); resolve(true); };
    const onNo  = () => { cleanup(); resolve(false); };

    yes.addEventListener('click', onYes);
    no.addEventListener('click', onNo);
    x.addEventListener('click', onNo);
  });
}

function smartTruncate(filename, maxLen = 100) {
  if (!filename || filename.length <= maxLen) return filename || '';
  const extIndex = filename.lastIndexOf('.');
  const ext = extIndex !== -1 ? filename.slice(extIndex) : '';
  const base = extIndex !== -1 ? filename.slice(0, extIndex) : filename;
  const keep = maxLen - ext.length - 3;
  const startLen = Math.ceil(keep / 2);
  const endLen = Math.floor(keep / 2);
  return base.slice(0, startLen) + '...' + base.slice(-endLen) + ext;
}

function buildResultItem(rec) {
  const row = document.createElement('div');
  row.className = 'result-item';
  row.dataset.path = rec.path;

  // filename (truncated for display only)
  const name = document.createElement('div');
  name.className = 'name';
  name.textContent = smartTruncate(rec.file, 100);

  // actions area
  const actions = document.createElement('div');
  actions.className = 'icon-row';

  // Download button
  const dl = document.createElement('a');
  dl.className = 'icon-btn';
  dl.href = `/static-serve?path=${encodeURIComponent(rec.path)}&download=1`;
  dl.setAttribute('title', 'Download');
  dl.innerHTML = `<i class="fa-solid fa-download" aria-hidden="true"></i><span class="sr-only">Download</span>`;
  actions.appendChild(dl);

  if (CASEORG_IS_ADMIN) {
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'icon-btn';
    del.setAttribute('title', 'Delete');
    del.innerHTML = `<i class="fa-solid fa-trash" aria-hidden="true"></i><span class="sr-only">Delete</span>`;
    del.addEventListener('click', async () => {
      const displayName = smartTruncate(rec.file, 100);
      const ok = await openConfirm(`Delete “${displayName}”?`);
      if (!ok) return;

      try {
        const resp = await fetch('/api/delete-file', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: rec.path })
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data.ok) {
          const msg = data && data.msg ? data.msg : `HTTP ${resp.status}`;
          alert(`Delete failed: ${msg}`);
          return;
        }
        row.remove();
      } catch (e) {
        alert(`Delete failed: ${e}`);
      }
    });
    actions.appendChild(del);
  }

  // double-click downloads
  row.addEventListener('dblclick', () => dl.click());

  // assemble row
  row.appendChild(name);
  row.appendChild(actions);
  return row;
}

function cloneResults(list) {
  if (!Array.isArray(list)) return null;
  return list.map(item => ({ ...item }));
}

let lastRenderedResults = null;

const dirSearchState = {
  active: false,
  previousScroll: 0,
  currentPath: ''
};

function renderResults(list) {
  const host = document.getElementById('results');
  if (!host) return;

  if (dirSearchState.active) {
    dirSearchState.active = false;
    dirSearchState.previousScroll = 0;
    dirSearchState.currentPath = '';
    const dirBtn = document.getElementById('dir-search');
    if (dirBtn) {
      dirBtn.classList.remove('active');
      dirBtn.textContent = 'Directory Search';
      dirBtn.setAttribute('aria-pressed', 'false');
    }
  }

  lastRenderedResults = cloneResults(list);
  host.innerHTML = '';
  if (!list || !list.length) {
    const empty = document.createElement('div');
    empty.className = 'result-item';
    empty.textContent = 'No results.';
    host.appendChild(empty);
    return;
  }
  list.forEach(rec => host.appendChild(buildResultItem(rec)));
}

// ------------- Directory tree (optional button #dir-search) -------------
async function showDirLevel(relPath) {
  if (!dirSearchState.active) return;
  const results = document.getElementById('results');
  if (!results) return;

  dirSearchState.currentPath = relPath || '';

  const url = new URL('/api/dir-tree', location.origin);
  if (relPath) url.searchParams.set('path', relPath);

  try {
    const resp = await fetch(url.toString());
    const data = await resp.json().catch(() => ({}));
    if (!dirSearchState.active) return;
    results.innerHTML = '';

    // Up directory
    if (relPath) {
      const up = document.createElement('div');
      up.className = 'result-item folder';
      up.innerHTML = `<i class="fa-solid fa-arrow-up" style="margin-right:6px;"></i> ..`;
      up.addEventListener('click', () => {
        const parts = relPath.split('/');
        parts.pop();
        showDirLevel(parts.join('/'));
      });
      results.appendChild(up);
    }

    // Directories
    (data.dirs || []).forEach(dir => {
      const row = document.createElement('div');
      row.className = 'result-item folder';
      row.innerHTML = `<i class="fa-solid fa-folder-open" style="color: var(--accent); margin-right:6px;"></i> ${dir}`;
      row.addEventListener('click', () => {
        const newPath = relPath ? `${relPath}/${dir}` : dir;
        showDirLevel(newPath);
      });
      results.appendChild(row);
    });

    // Files
    (data.files || []).forEach(f => {
      results.appendChild(buildResultItem({
        file: f.name,
        path: f.path,
        rel: f.name
      }));
    });

    if ((!data.dirs || !data.dirs.length) && (!data.files || !data.files.length)) {
      const empty = document.createElement('div');
      empty.className = 'result-item';
      empty.textContent = '(empty)';
      results.appendChild(empty);
    }
  } catch (e) {
    if (!dirSearchState.active) return;
    results.innerHTML = `<div class="result-item">Error: ${e}</div>`;
  }
}

// -------------------- Create Case form --------------------
function setActive(card, others){
  card.classList.add('active'); card.setAttribute('aria-pressed','true');
  others.forEach(c => { c.classList.remove('active'); c.setAttribute('aria-pressed','false'); });
}

function createCaseForm(){
  const host = $('#form-host');
  if (!host) return;
  host.innerHTML = '';
  const wrap = el('div','form-card');
  wrap.innerHTML = `
    <h3>Create Case</h3>
    <div class="form-grid">
      <input type="date" id="cc-date" />

      <!-- Parties -->
      <input type="text" id="pn" placeholder="Petitioner Name" />
      <input type="text" id="rn" placeholder="Respondent Name" />
      <input type="text" id="pa" placeholder="Petitioner Address" />
      <input type="text" id="ra" placeholder="Respondent Address" />
      <input type="text" id="pc" placeholder="Petitioner Contact" />
      <input type="text" id="rc" placeholder="Respondent Contact" />

      <!-- Auto Case Name (preview) -->
      <input type="text" id="cc-name-preview" placeholder="Case Name (auto)" disabled />
      <input type="hidden" id="cc-name" />

      <!-- Representing -->
      <div style="grid-column: span 2;">
        <label style="display:block;margin:6px 0 4px;">We’re Representing:</label>
        <select id="op"><option>Petitioner</option><option>Respondent</option></select>
      </div>

      <!-- Domain -> Subcategory -->
      <select id="cat"><option value="">Case Category</option><option>Criminal</option><option>Civil</option><option>Commercial</option></select>
      <select id="subcat" disabled><option value="">Subcategory</option></select>

      <!-- Case Type (domain-specific) -->
      <select id="ctype" disabled><option value="">Case Type</option></select>
      <input type="text" id="ctype-other" placeholder="Case Type (Other)" style="display:none;" />
      
      <!-- Courts -->
      <input type="text" id="os" placeholder="Origin State" />
      <input type="text" id="od" placeholder="Origin District" />
      <input type="text" id="of" placeholder="Origin Court/Forum" />
      <input type="text" id="cs" placeholder="Current State" />
      <input type="text" id="cd" placeholder="Current District" />
      <input type="text" id="cf" placeholder="Current Court/Forum" />

      <textarea id="an" rows="3" placeholder="Additional Notes" style="grid-column: span 2;"></textarea>
    </div>
    <div class="form-actions">
      <button id="cc-go" class="btn-primary" type="button">Create Case & Save Note</button>
    </div>
  `;
  host.append(wrap);

  // defaults
  const dateEl = $('#cc-date');
  if (dateEl) dateEl.valueAsDate = new Date();

  // Auto case name from PN/RN
  function updateCaseName(){
    const pn = ($('#pn')?.value || '').trim();
    const rn = ($('#rn')?.value || '').trim();
    const name = (pn && rn) ? `${pn} v. ${rn}` : '';
    const hidden = $('#cc-name');
    const preview = $('#cc-name-preview');
    if (hidden) hidden.value = name;
    if (preview) preview.value = name;
  }
  ['pn','rn'].forEach(id => $('#'+id)?.addEventListener('input', updateCaseName));
  updateCaseName();

  // Domain -> Subcategory -> CaseType
  $('#cat')?.addEventListener('change', () => {
    const dom = $('#cat').value || '';
    if (dom && SUBCATS[dom]) {
      populateOptions($('#subcat'), SUBCATS[dom], "Subcategory");
      populateOptions($('#ctype'), CASE_TYPES[dom], "Case Type");
      $('#ctype').disabled = false;
    } else {
      if ($('#subcat')) { $('#subcat').innerHTML = '<option value="">Subcategory</option>'; $('#subcat').disabled = true; }
      if ($('#ctype')) { $('#ctype').innerHTML = '<option value="">Case Type</option>'; $('#ctype').disabled = true; }
      if ($('#ctype-other')) $('#ctype-other').style.display = 'none';
    }
  });

  // Show text input only if Case Type == Others
  $('#ctype')?.addEventListener('change', () => {
    const val = $('#ctype').value || '';
    if ($('#ctype-other')) $('#ctype-other').style.display = (val === 'Others') ? 'block' : 'none';
  });

  // Submit
  $('#cc-go')?.addEventListener('click', async ()=>{
    const fd = new FormData();
    fd.set('Date', $('#cc-date')?.value || '');
    fd.set('Case Name', $('#cc-name')?.value || '');  // auto-built
    fd.set('Petitioner Name', ($('#pn')?.value || '').trim());
    fd.set('Petitioner Address', ($('#pa')?.value || '').trim());
    fd.set('Petitioner Contact', ($('#pc')?.value || '').trim());
    fd.set('Respondent Name', ($('#rn')?.value || '').trim());
    fd.set('Respondent Address', ($('#ra')?.value || '').trim());
    fd.set('Respondent Contact', ($('#rc')?.value || '').trim());
    fd.set('Our Party', $('#op')?.value || '');
    const cat = $('#cat')?.value || '';
    const subcat = $('#subcat')?.value || '';
    fd.set('Case Category', cat);
    fd.set('Case Subcategory', subcat);
    const ctypeSel = $('#ctype')?.value || '';
    const ctype = (ctypeSel === 'Others') ? (($('#ctype-other')?.value || '').trim()) : ctypeSel;
    fd.set('Case Type', ctype);
    fd.set('Origin State', ($('#os')?.value || '').trim());
    fd.set('Origin District', ($('#od')?.value || '').trim());
    fd.set('Origin Court/Forum', ($('#of')?.value || '').trim());
    fd.set('Current State', ($('#cs')?.value || '').trim());
    fd.set('Current District', ($('#cd')?.value || '').trim());
    fd.set('Current Court/Forum', ($('#cf')?.value || '').trim());
    fd.set('Additional Notes', ($('#an')?.value || '').trim());

    if (!($('#cc-name')?.value)) { alert('Enter Petitioner and Respondent to form the Case Name.'); return; }

    const r = await fetch('/create-case', { method: 'POST', body: fd });
    const data = await r.json().catch(()=>({ok:false,msg:'Bad JSON'}));
    alert(data.ok ? 'Case created at: ' + data.path : ('Error: ' + (data.msg || 'Failed')));
  });
}

// -------------------- Manage Case form --------------------
function manageCaseForm(){
  const host = $('#form-host');
  if (!host) return;
  host.innerHTML = '';
  const wrap = el('div','form-card manage-case-card');
  wrap.innerHTML = `
    <h3 class="section-title">Manage Case</h3>
    <div class="mc-tabs" role="tablist" aria-label="Manage case lookup">
      <button type="button" class="mc-tab active" data-tab="date" role="tab" aria-selected="true">Year & Month</button>
      <button type="button" class="mc-tab" data-tab="name" role="tab" aria-selected="false">Case Name</button>
    </div>
    <div class="mc-panel" data-tab="date">
      <div class="form-grid">
        <select id="mc-year"><option value="">Year</option></select>
        <select id="mc-month" disabled><option value="">Month</option></select>
        <select id="mc-case" disabled><option value="">Case (Petitioner v. Respondent)</option></select>
        <select id="domain">
          <option value="">File Category</option>
          <option>Criminal</option><option>Civil</option><option>Commercial</option><option>Case Law</option><option>Invoices</option>
        </select>
        <select id="subcategory" disabled><option value="">Subcategory</option></select>
        <input type="text" id="main-type" placeholder="Main Type (e.g., Transfer Petition, Criminal Revision, Orders)" />
        <input type="date" id="mc-date" />
        <button id="create-note-btn" class="btn-secondary" type="button" hidden>
          View / Edit Note.json
        </button>
      </div>
    </div>
    <div class="mc-panel" data-tab="name" hidden>
      <div class="mc-name-search">
        <input type="text" id="mc-name-input" placeholder="Search case name…" />
        <button type="button" id="mc-name-search" class="btn-secondary">Search</button>
      </div>
      <div id="mc-name-results" class="results mc-name-results">
        <div class="result-item">Search for a case to begin.</div>
      </div>
    </div>

    <div class="dropzone" id="drop" tabindex="0">Drag & drop files here or click to select</div>
    <input type="file" id="file" hidden accept=".pdf,.docx,.txt,.png,.jpg,.jpeg,.json" multiple />
    <div id="file-list" class="results"></div>

    <div class="form-actions">
      <button id="mc-go" class="btn-primary" type="button">Upload & Categorize File(s)</button>
      <button id="mc-invoice" class="btn-secondary" type="button" disabled>Generate Invoice</button>
    </div>
  `;
  host.append(wrap);

  // defaults
  const mcDate = $('#mc-date'); if (mcDate) mcDate.valueAsDate = new Date();

  // --- Populate Year / Month / Case from backend -----------------------
  const yearSel  = $('#mc-year');
  const monthSel = $('#mc-month');
  const caseSel  = $('#mc-case');
  const noteBtn  = $('#create-note-btn');
  const invoiceBtn = $('#mc-invoice');
  const nameInput = $('#mc-name-input');
  const nameBtn = $('#mc-name-search');
  const nameResults = $('#mc-name-results');

  const setVisibility = (el, show) => {
    if (!el) return;
    if (show) {
      el.classList.remove('is-hidden');
      el.removeAttribute('hidden');
    } else {
      el.classList.add('is-hidden');
      el.setAttribute('hidden', '');
    }
  };

  setVisibility(noteBtn, false);
  if (noteBtn) {
    noteBtn.dataset.hasNote && delete noteBtn.dataset.hasNote;
    noteBtn.dataset.intent && delete noteBtn.dataset.intent;
    noteBtn.onclick = null;
  }

  function activateTab(target){
    const tabs = Array.from(document.querySelectorAll('.mc-tab'));
    const panels = Array.from(document.querySelectorAll('.mc-panel'));
    tabs.forEach(tab => {
      const active = tab.dataset.tab === target;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', String(active));
    });
    panels.forEach(panel => {
      panel.hidden = panel.dataset.tab !== target;
    });
  }

  Array.from(document.querySelectorAll('.mc-tab')).forEach(tab => {
    tab.addEventListener('click', () => {
      if (!tab.dataset.tab) return;
      activateTab(tab.dataset.tab);
    });
  });
  activateTab('date');

  async function loadYears(){
    const r = await fetch('/api/years');
    const data = await r.json().catch(()=>({years:[]}));
    yearSel.innerHTML = '<option value="">Year</option>';
    (data.years || []).forEach(y => {
      const o = el('option'); o.value = y; o.textContent = y; yearSel.append(o);
    });
    yearSel.disabled = false;
    monthSel.innerHTML = '<option value="">Month</option>'; monthSel.disabled = true;
    caseSel.innerHTML  = '<option value="">Case (Petitioner v. Respondent)</option>'; caseSel.disabled = true;
    updateCaseActions();
  }

  async function loadMonths(year){
    const r = await fetch(`/api/months?${new URLSearchParams({year})}`);
    const data = await r.json().catch(()=>({months:[]}));
    monthSel.innerHTML = '<option value="">Month</option>';
    (data.months || []).forEach(m => {
      const o = el('option'); o.value = m; o.textContent = m; monthSel.append(o);
    });
    monthSel.disabled = false;
    caseSel.innerHTML  = '<option value="">Case (Petitioner v. Respondent)</option>'; caseSel.disabled = true;
    updateCaseActions();
  }

  async function loadCases(year, month){
    const r = await fetch(`/api/cases?${new URLSearchParams({year, month})}`);
    const data = await r.json().catch(()=>({cases:[]}));
    caseSel.innerHTML = '<option value="">Case (Petitioner v. Respondent)</option>';
    (data.cases || []).forEach(cn => {
      const o = el('option'); o.value = cn; o.textContent = cn; caseSel.append(o);
    });
    caseSel.disabled = false;
    updateCaseActions();
  }

  yearSel.addEventListener('change', () => {
    const y = yearSel.value || '';
    if (!y){
      monthSel.innerHTML = '<option value="">Month</option>'; monthSel.disabled = true;
      caseSel.innerHTML  = '<option value="">Case (Petitioner v. Respondent)</option>'; caseSel.disabled = true;
      updateCaseActions();
      return;
    }
    loadMonths(y);
  });

  monthSel.addEventListener('change', () => {
    const y = yearSel.value || ''; const m = monthSel.value || '';
    if (y && m) loadCases(y, m);
    else { caseSel.innerHTML = '<option value="">Case (Petitioner v. Respondent)</option>'; caseSel.disabled = true; updateCaseActions(); }
  });

  caseSel.addEventListener('change', updateCaseActions);

  if (invoiceBtn) {
    invoiceBtn.style.marginLeft = 'auto';
    invoiceBtn.disabled = true;
    invoiceBtn.setAttribute('aria-disabled', 'true');
    invoiceBtn.addEventListener('click', () => {
      if (invoiceBtn.disabled) return;
      const year = yearSel.value || '';
      const month = monthSel.value || '';
      const cname = caseSel.value || '';
      if (!year || !month || !cname) return;
      const params = new URLSearchParams({ year, month, case: cname });
      window.location.href = `/invoice?${params.toString()}`;
    });
  }

  // --- Notes presence check + button wiring -----------------
  async function getNoteState(year, month, cname) {
      try {
          const resp = await fetch(`/api/note/${year}/${month}/${encodeURIComponent(cname)}`);
          const data = await resp.json().catch(()=>null);
          if (resp.ok && data?.ok) {
              return {
                  exists: true,
                  content: data.content || '',
                  template: data.template || defaultNoteTemplate()
              };
          }
          return {
              exists: false,
              content: '',
              template: (data && data.template) || defaultNoteTemplate()
          };
      } catch (err) {
          console.warn('Note check failed', err);
          return { exists: false, content: '', template: defaultNoteTemplate() };
      }
  }

  async function updateCaseActions() {
      const year  = yearSel.value || '';
      const month = monthSel.value || '';
      const cname = caseSel.value || '';
      const hasSelection = Boolean(year && month && cname);

      if (invoiceBtn) {
          invoiceBtn.disabled = !hasSelection;
          invoiceBtn.setAttribute('aria-disabled', hasSelection ? 'false' : 'true');
      }

      if (!noteBtn) return;

      if (!hasSelection) {
          setVisibility(noteBtn, false);
          delete noteBtn.dataset.hasNote;
          delete noteBtn.dataset.intent;
          noteBtn.onclick = null;
          return;
      }

      const noteState = await getNoteState(year, month, cname);
      if (!noteState.exists) {
          setVisibility(noteBtn, false);
          delete noteBtn.dataset.hasNote;
          delete noteBtn.dataset.intent;
          noteBtn.onclick = null;
          return;
      }

      setVisibility(noteBtn, true);
      noteBtn.dataset.hasNote = '1';
      noteBtn.dataset.intent = 'update';
      noteBtn.textContent = 'View / Edit Note.json';
      noteBtn.onclick = async () => {
          const currentState = await getNoteState(yearSel.value || '', monthSel.value || '', caseSel.value || '');
          if (!currentState.exists) {
              alert('Note.json not found for this case.');
              updateCaseActions();
              return;
          }
          const context = {
              kind: 'case',
              year: yearSel.value || '',
              month: monthSel.value || '',
              caseName: caseSel.value || ''
          };
          openNotesModal(currentState.content || '', 'update', context);
      };
  }

  // expose so the modal save handler can refresh after writes
  window.__refreshNoteButton = updateCaseActions;

  async function renderNameResults(list){
    if (!nameResults) return;
    nameResults.innerHTML = '';
    if (!Array.isArray(list) || !list.length) {
      nameResults.innerHTML = '<div class="result-item">No cases found.</div>';
      return;
    }
    list.forEach(item => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'result-item mc-name-result';
      btn.innerHTML = `
        <div class="name">${escapeHtml(item.case)}</div>
        <div class="meta">${escapeHtml(item.month)} ${escapeHtml(item.year)}</div>
      `;
      btn.addEventListener('click', async () => {
        activateTab('date');
        if (!yearSel || !monthSel || !caseSel) return;
        if (!Array.from(yearSel.options).some(opt => opt.value === item.year)) {
          await loadYears();
        }
        yearSel.value = item.year;
        await loadMonths(item.year);
        monthSel.value = item.month;
        await loadCases(item.year, item.month);
        caseSel.value = item.case;
        caseSel.dispatchEvent(new Event('change'));
        updateCaseActions();
      });
      nameResults.append(btn);
    });
  }

  async function performNameSearch(){
    if (!nameInput || !nameResults) return;
    const q = nameInput.value.trim();
    if (!q) {
      alert('Enter a case name to search.');
      nameInput.focus();
      return;
    }
    nameResults.innerHTML = '<div class="result-item">Searching…</div>';
    try {
      const resp = await fetch(`/api/cases/search?${new URLSearchParams({q})}`);
      const data = await resp.json().catch(()=>({}));
      if (!resp.ok) {
        throw new Error(data.error || `HTTP ${resp.status}`);
      }
      renderNameResults(data.cases || []);
    } catch (err) {
      nameResults.innerHTML = `<div class="result-item">Search failed: ${escapeHtml(err.message || err)}</div>`;
    }
  }

  nameBtn?.addEventListener('click', performNameSearch);
  nameInput?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      performNameSearch();
    }
  });


  // Load initial years
  loadYears();

  // --- Domain -> Subcategory ------------------------------------------
  $('#domain')?.addEventListener('change', () => {
    const dom = $('#domain').value || '';
    const subSel = $('#subcategory');
    const mt = $('#main-type');

    if (dom === 'Case Law') {
      if (subSel) { subSel.innerHTML = '<option value="">Subcategory (not used for Case Law)</option>'; subSel.disabled = true; }
      if (mt) mt.placeholder = 'Case Law title / citation (used as filename)';
      return;
    }
    if (dom === 'Invoices') {
      if (subSel) { subSel.innerHTML = '<option value="">Subcategory (not used for Invoices)</option>'; subSel.disabled = true; }
      if (mt) mt.placeholder = 'Main Type (e.g., Transfer Petition, Criminal Revision, Orders)';
      return;
    }

    if (mt) mt.placeholder = 'Main Type (e.g., Transfer Petition, Criminal Revision, Orders)';
    if (dom && SUBCATS[dom]) {
      populateOptions(subSel, SUBCATS[dom], "Subcategory");
    } else if (subSel) {
      subSel.innerHTML = '<option value="">Subcategory</option>'; subSel.disabled = true;
    }
  });

  $('#subcategory')?.addEventListener('change', () => {
    const val = ($('#subcategory')?.value || '').toLowerCase();
    const mt  = $('#main-type');
    if (!mt) return;
    if (val === 'primary documents') {
      mt.value = '';
      mt.disabled = true;
      mt.placeholder = 'Main Type (not used for Primary Documents)';
    } else {
      mt.disabled = false;
      if (($('#domain')?.value || '') !== 'Case Law') {
        mt.placeholder = 'Main Type (e.g., Transfer Petition, Criminal Revision, Orders)';
      }
    }
  });

  // --- File selection / upload -----------------------------------------
  const dz = $('#drop');
  const fileInput = $('#file');
  const fileList  = $('#file-list');
  let selectedFiles = [];

  function renderSelected(){
    if (!fileList) return;
    fileList.innerHTML = '';
    if (!selectedFiles.length){ fileList.textContent = 'No files selected.'; return; }
    selectedFiles.forEach((f, idx) => {
      const row = el('div','result-item');
      const name = el('div'); name.textContent = f.name;
      const meta = el('span','badge'); meta.textContent = `${(f.size/1024).toFixed(1)} KB`;
      const rm = el('button'); rm.type = 'button'; rm.textContent = '✕'; rm.className = 'btn-ghost';
      rm.style.padding = '4px 8px'; rm.style.marginLeft = 'auto';
      rm.addEventListener('click', ()=>{ selectedFiles.splice(idx,1); renderSelected(); });
      row.append(name, meta, rm);
      fileList.append(row);
    });
  }

  function chooseFiles(){ fileInput?.click(); }
  dz?.addEventListener('click', chooseFiles);
  dz?.addEventListener('keydown', (e)=>{ if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); chooseFiles(); }});
  fileInput?.addEventListener('change', ()=>{ selectedFiles = Array.from(fileInput.files || []); renderSelected(); });
  dz?.addEventListener('dragover', e=>{ e.preventDefault(); dz.classList.add('dragover'); });
  dz?.addEventListener('dragleave', ()=> dz.classList.remove('dragover'));
  dz?.addEventListener('drop', e=>{
    e.preventDefault(); dz.classList.remove('dragover');
    const files = Array.from(e.dataTransfer.files || []);
    if (!files.length) return;
    const key = f => `${f.name}-${f.size}`;
    const have = new Set(selectedFiles.map(key));
    files.forEach(f => { if (!have.has(key(f))) selectedFiles.push(f); });
    renderSelected();
  });

  $('#mc-go')?.addEventListener('click', async ()=>{
    const year  = yearSel.value || '';
    const month = monthSel.value || '';
    const cname = caseSel.value || '';
    if (!year || !month || !cname){ alert('Select Year, Month, and Case.'); return; }
    if (!selectedFiles.length){ alert('Select at least one file'); return; }

    const fd = new FormData();
    fd.set('Year', year);
    fd.set('Month', month);
    fd.set('Case Name', cname);
    fd.set('Domain', $('#domain')?.value || '');
    fd.set('Subcategory', $('#subcategory')?.value || '');
    fd.set('Main Type', ($('#main-type')?.value || '').trim());
    fd.set('Date', $('#mc-date')?.value || '');
    selectedFiles.forEach(f => fd.append('file', f));

    const r = await fetch('/manage-case/upload', { method: 'POST', body: fd });
    const data = await r.json().catch(()=>({ok:false,msg:'Bad JSON'}));
    if (data.ok) {
      const saved = Array.isArray(data.saved_as) ? data.saved_as.join('\n') : data.saved_as;
      alert('Saved:\n' + saved);
      selectedFiles = []; if (fileInput) fileInput.value = ''; renderSelected();
    } else {
      alert('Error: ' + (data.msg || 'Upload failed'));
    }
  });

  renderSelected();
}

function caseLawUploadForm(){
  const host = $('#form-host');
  if (!host) return;
  host.innerHTML = '';

  const wrap = el('div', 'form-card');
  wrap.innerHTML = `
    <h3>Upload Case Law</h3>
    <div class="form-grid">
      <input type="text" id="clu-petitioner" placeholder="Petitioner Name" />
      <input type="text" id="clu-respondent" placeholder="Respondent Name" />
      <input type="text" id="clu-citation" placeholder="Citation" />
      <select id="clu-year"><option value="">Decision Year</option></select>
      <div class="clu-type-row full-span">
        <select id="clu-primary"><option value="">Primary Type</option></select>
        <select id="clu-case-type" disabled><option value="">Case Type</option></select>
        <label class="file-field" for="clu-file">
          <input type="file" id="clu-file" class="file-input" accept=".pdf,.docx,.txt,.png,.jpg,.jpeg,.json" />
          <span id="clu-file-label">Select judgment file…</span>
          <button type="button" class="btn-secondary file-btn" id="clu-file-btn">Browse</button>
        </label>
      </div>
      <textarea id="clu-note" class="full-span" rows="4" placeholder="Brief Note / Summary"></textarea>
    </div>
    <div class="form-actions">
      <button id="clu-submit" class="btn-primary" type="button">Upload Case Law</button>
    </div>
  `;
  host.append(wrap);

  const primarySel = $('#clu-primary');
  const caseTypeSel = $('#clu-case-type');
  const yearSel = $('#clu-year');

  if (primarySel) {
    populateOptions(primarySel, Object.keys(CASE_TYPES), 'Primary Type');
  }

  if (yearSel) {
    yearSel.innerHTML = '<option value="">Decision Year</option>';
    const currentYear = new Date().getFullYear();
    for (let y = currentYear; y >= 1950; y--) {
      const opt = el('option');
      opt.value = String(y);
      opt.textContent = String(y);
      yearSel.append(opt);
    }
  }

  if (caseTypeSel) {
    caseTypeSel.innerHTML = '<option value="">Case Type</option>';
    caseTypeSel.disabled = true;
  }

  primarySel?.addEventListener('change', () => {
    const val = primarySel.value || '';
    if (val && CASE_TYPES[val]) {
      populateOptions(caseTypeSel, CASE_TYPES[val], 'Case Type');
    } else if (caseTypeSel) {
      caseTypeSel.innerHTML = '<option value="">Case Type</option>';
      caseTypeSel.disabled = true;
    }
  });

  const fileInput = document.getElementById('clu-file');
  const fileLabel = document.getElementById('clu-file-label');
  const fileBtn = document.getElementById('clu-file-btn');
  const fileField = wrap.querySelector('.file-field');
  if (fileBtn && fileInput) {
    fileBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
      const file = fileInput.files?.[0];
      fileLabel.textContent = file ? file.name : 'Select judgment file…';
    });
  }

  if (fileField && fileInput) {
    const setFile = (file) => {
      if (!file) return;
      const dt = new DataTransfer();
      dt.items.add(file);
      fileInput.files = dt.files;
      fileLabel.textContent = file.name;
    };

    ['dragenter','dragover'].forEach(evt => {
      fileField.addEventListener(evt, (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
        fileField.classList.add('dragover');
      });
    });

    ['dragleave','dragend'].forEach(evt => {
      fileField.addEventListener(evt, () => {
        fileField.classList.remove('dragover');
      });
    });

    fileField.addEventListener('drop', (e) => {
      e.preventDefault();
      const files = e.dataTransfer?.files;
      if (files && files.length) {
        setFile(files[0]);
      }
      fileField.classList.remove('dragover');
    });
  }

  const submitBtn = $('#clu-submit');
  submitBtn?.addEventListener('click', async () => {
    const petitioner = ($('#clu-petitioner')?.value || '').trim();
    const respondent = ($('#clu-respondent')?.value || '').trim();
    const citation = ($('#clu-citation')?.value || '').trim();
    const year = ($('#clu-year')?.value || '').trim();
    const primary = ($('#clu-primary')?.value || '').trim();
    const caseType = ($('#clu-case-type')?.value || '').trim();
    const note = ($('#clu-note')?.value || '').trim();
    const file = fileInput?.files?.[0];

    if (!petitioner || !respondent) { alert('Petitioner and Respondent are required.'); return; }
    if (!citation) { alert('Citation is required.'); return; }
    if (!year) { alert('Decision year is required.'); return; }
    if (!primary) { alert('Select a primary classification.'); return; }
    if (!caseType) { alert('Select a case type.'); return; }
    if (!note) { alert('Please provide a brief note.'); return; }
    if (!file) { alert('Select a judgment file to upload.'); return; }

    const fd = new FormData();
    fd.set('petitioner', petitioner);
    fd.set('respondent', respondent);
    fd.set('citation', citation);
    fd.set('decision_year', year);
    fd.set('primary_type', primary);
    fd.set('case_type', caseType);
    fd.set('note', note);
    fd.append('file', file);

    try {
      const resp = await fetch('/case-law/upload', { method: 'POST', body: fd });
      const data = await resp.json().catch(()=>({}));
      if (!resp.ok || !data.ok) {
        throw new Error(data.msg || `HTTP ${resp.status}`);
      }
      alert('Case law uploaded successfully.');
      ['clu-petitioner','clu-respondent','clu-citation','clu-note'].forEach(id => {
        const elField = document.getElementById(id);
        if (elField) elField.value = '';
      });
      if (primarySel) primarySel.selectedIndex = 0;
      if (caseTypeSel) {
        caseTypeSel.innerHTML = '<option value="">Case Type</option>';
        caseTypeSel.disabled = true;
      }
      if (yearSel) yearSel.selectedIndex = 0;
      if (fileInput) {
        fileInput.value = '';
        fileLabel.textContent = 'Select judgment file…';
      }
    } catch (err) {
      alert(`Upload failed: ${err.message || err}`);
    }
  });
}

function caseLawSearchForm(){
  const host = $('#form-host');
  if (!host) return;
  host.innerHTML = '';

  const wrap = el('div', 'form-card');
  wrap.innerHTML = `
    <div class="cls-mode-header">
      <h3>Search Case Law</h3>
      <div class="cls-mode-tabs" role="tablist" aria-label="Case law search mode">
        <button type="button" class="cls-mode-tab active" data-mode="name" role="tab" aria-selected="true">Name</button>
        <!-- <button type="button" class="cls-mode-tab" data-mode="citation" role="tab" aria-selected="false">Citation</button> -->
        <button type="button" class="cls-mode-tab" data-mode="year" role="tab" aria-selected="false">Year</button>
        <button type="button" class="cls-mode-tab" data-mode="type" role="tab" aria-selected="false">Type</button>
        <button type="button" class="cls-mode-tab" data-mode="advanced" role="tab" aria-selected="false">Advanced</button>
      </div>
    </div>
    <input type="radio" name="cls-mode" value="name" checked hidden>
    <!-- <input type="radio" name="cls-mode" value="citation" hidden> -->
    <input type="radio" name="cls-mode" value="year" hidden>
    <input type="radio" name="cls-mode" value="type" hidden>
    <input type="radio" name="cls-mode" value="advanced" hidden>
    <div class="form-grid cls-form">
      <div class="cls-mode-panel" data-mode="name">
        <div class="cl-name-row">
          <label class="cl-name-option">
            <input type="radio" name="cls-name-mode" value="petitioner" data-target="cls-name-petitioner" checked>
            <span>Petitioner</span>
            <input type="text" id="cls-name-petitioner" class="cl-name-input" placeholder="Petitioner Name" />
          </label>
          <label class="cl-name-option">
            <input type="radio" name="cls-name-mode" value="respondent" data-target="cls-name-respondent">
            <span>Respondent</span>
            <input type="text" id="cls-name-respondent" class="cl-name-input" placeholder="Respondent Name" disabled />
          </label>
          <label class="cl-name-option">
            <input type="radio" name="cls-name-mode" value="either" data-target="cls-name-either">
            <span>Either Party</span>
            <input type="text" id="cls-name-either" class="cl-name-input" placeholder="Either Party Name" disabled />
          </label>
        </div>
      </div>
      <!-- <div class="cls-mode-panel" data-mode="citation" hidden>
        <input type="text" id="cls-citation" placeholder="Citation" />
      </div> -->
      <div class="cls-mode-panel" data-mode="year" hidden>
        <select id="cls-year"><option value="">Decision Year</option></select>
      </div>
      <div class="cls-mode-panel full-span" data-mode="type" hidden>
        <div class="cls-type-row">
          <select id="cls-primary"><option value="">Primary Type</option></select>
          <select id="cls-case-type" disabled><option value="">Case Type</option></select>
        </div>
      </div>
      <div class="cls-mode-panel full-span" data-mode="advanced" hidden>
        <textarea id="cls-text" rows="3" placeholder="Enter boolean query, e.g. bail AND 498A NOT dowry or maintenance NEAR/5 interim"></textarea>
        <p class="form-help">Boolean operators (AND/OR/NOT) and proximity syntax (term NEAR/5 term) are supported.</p>
      </div>
    </div>
    <div class="form-actions form-actions-right">
      <button id="cls-search" class="btn-primary" type="button">Search</button>
      <button id="cls-reset" class="btn-ghost" type="button">Reset</button>
    </div>
    <div id="cls-results" class="results"></div>
  `;
  host.append(wrap);

  const resultsHost = $('#cls-results');
  const modeRadios = Array.from(document.querySelectorAll('input[name="cls-mode"]'));
  const modeTabs = Array.from(document.querySelectorAll('.cls-mode-tab'));
  const panels = Array.from(document.querySelectorAll('.cls-mode-panel'));

  const nameModeRadios = Array.from(document.querySelectorAll('input[name="cls-name-mode"]'));
  const citationInput = $('#cls-citation');
  const yearSel = $('#cls-year');
  const primarySel = $('#cls-primary');
  const caseTypeSel = $('#cls-case-type');
  const textInput = $('#cls-text');
  const nameTextInputs = nameModeRadios.map(radio => {
    const targetId = radio.dataset.target;
    return targetId ? document.getElementById(targetId) : null;
  });

  if (primarySel) populateOptions(primarySel, Object.keys(CASE_TYPES), 'Primary Type');
  if (caseTypeSel) {
    caseTypeSel.innerHTML = '<option value="">Case Type</option>';
    caseTypeSel.disabled = true;
  }

  if (resultsHost) {
    resultsHost.innerHTML = '<div class="result-item">Use the search tools above to view results.</div>';
  }

  function showPanel(mode){
    panels.forEach(panel => {
      panel.hidden = panel.dataset.mode !== mode;
    });
  }

  function updateNameInputs(){
    let activeInput = null;
    nameModeRadios.forEach(radio => {
      const targetId = radio.dataset.target;
      const input = targetId ? document.getElementById(targetId) : null;
      if (!input) return;
      if (radio.checked) {
        input.disabled = false;
        activeInput = input;
      } else {
        input.disabled = true;
      }
    });
    if (activeInput) activeInput.focus();
  }

  updateNameInputs();

  nameModeRadios.forEach(radio => {
    radio.addEventListener('change', () => {
      if (document.querySelector('input[name="cls-mode"]:checked')?.value === 'name') {
        updateNameInputs();
      }
    });
  });

  function activateMode(mode){
    modeTabs.forEach(tab => {
      const active = tab.dataset.mode === mode;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', String(active));
    });
    modeRadios.forEach(radio => {
      radio.checked = radio.value === mode;
    });
    showPanel(mode);
    if (mode === 'advanced') {
      textInput?.focus();
    }
    if (mode === 'name') {
      updateNameInputs();
    }
    if (mode === 'type') {
      if (primarySel && primarySel.childElementCount === 0) {
        populateOptions(primarySel, Object.keys(CASE_TYPES), 'Primary Type');
      }
    } else if (caseTypeSel) {
      caseTypeSel.disabled = true;
    }
    if (mode !== 'name') {
      nameModeRadios.forEach(r => {
        const targetId = r.dataset.target;
        const input = targetId ? document.getElementById(targetId) : null;
        if (input) input.disabled = true;
      });
    }
  }

  modeTabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const mode = tab.dataset.mode;
      if (mode) activateMode(mode);
    });
  });

  modeRadios.forEach(radio => {
    radio.addEventListener('change', () => {
      if (radio.checked) activateMode(radio.value);
    });
  });

  activateMode('name');

  primarySel?.addEventListener('change', () => {
    const val = primarySel.value || '';
    if (val && CASE_TYPES[val]) {
      populateOptions(caseTypeSel, CASE_TYPES[val], 'Case Type');
    } else if (caseTypeSel) {
      caseTypeSel.innerHTML = '<option value="">Case Type</option>';
      caseTypeSel.disabled = true;
    }
  });

  function applyFilters(filters){
    if (yearSel) {
      const selected = yearSel.value;
      yearSel.innerHTML = '';
      const placeholder = el('option');
      placeholder.value = '';
      placeholder.textContent = 'Decision Year';
      yearSel.append(placeholder);
      const years = Array.isArray(filters?.years) ? filters.years : [];
      years.forEach(year => {
        const opt = el('option');
        opt.value = year;
        opt.textContent = year;
        yearSel.append(opt);
      });
      yearSel.value = selected || '';
    }
  }

  function renderResults(list){
    if (!resultsHost) return;
    resultsHost.innerHTML = '';
    if (!Array.isArray(list) || !list.length) {
      resultsHost.innerHTML = '<div class="result-item">No case law found.</div>';
      return;
    }

    list.forEach(item => {
      const card = el('div', 'result-item case-law-card');
      const title = `${item.petitioner} vs ${item.respondent} [${item.citation}]`;
      const metaParts = [item.primary_type, item.case_type, item.decision_year];
      const meta = metaParts.filter(Boolean).join(' · ');
      const notePreview = item.note_preview || 'No note saved yet.';
      const textPreview = item.text_preview || '';

      const head = el('div', 'cl-card-head');
      const headMain = el('div', 'cl-card-head-main');
      const titleEl = el('div', 'cl-card-title');
      titleEl.textContent = title;
      const metaEl = el('div', 'cl-card-meta');
      metaEl.textContent = meta;
      headMain.append(titleEl, metaEl);
      head.append(headMain);

      let deleteBtn = null;
      if (CASEORG_IS_ADMIN) {
        deleteBtn = el('button', 'cl-delete-btn');
        deleteBtn.type = 'button';
        deleteBtn.textContent = '✕';
        deleteBtn.setAttribute('title', 'Delete case law entry');
        deleteBtn.setAttribute('aria-label', 'Delete case law entry');
        head.append(deleteBtn);
      }

      const body = el('div', 'cl-card-body');
      body.innerHTML = `
        <div class="cl-snippet">${escapeHtml(textPreview)}</div>
        <div class="cl-note-preview cl-muted">${escapeHtml(notePreview)}</div>
      `;

      const actionsRow = el('div', 'cl-card-actions');
      const downloadLink = document.createElement('a');
      downloadLink.className = 'btn-secondary cl-download';
      downloadLink.href = item.download_url;
      downloadLink.target = '_blank';
      downloadLink.rel = 'noopener';
      downloadLink.textContent = 'Download Judgment';

      const noteBtn = el('button', 'btn-primary cl-note');
      noteBtn.type = 'button';
      noteBtn.textContent = 'View / Edit Note';

      actionsRow.append(downloadLink, noteBtn);

      card.append(head, body, actionsRow);

      if (deleteBtn) {
        deleteBtn.addEventListener('click', async () => {
          const confirmMessage = `Delete "${title}"? This will remove the stored files and note.`;
          const ok = await openConfirm(confirmMessage);
          if (!ok) return;

          try {
            const resp = await fetch(`/case-law/${item.id}`, { method: 'DELETE' });
            const data = await resp.json().catch(()=>({}));
            if (!resp.ok || !data.ok) {
              throw new Error(data.msg || `HTTP ${resp.status}`);
            }
            card.remove();
            if (resultsHost && !resultsHost.querySelector('.case-law-card')) {
              resultsHost.innerHTML = '<div class="result-item">No case law found.</div>';
            }
          } catch (err) {
            alert(`Delete failed: ${err.message || err}`);
          }
        });
      }

      const previewEl = body.querySelector('.cl-note-preview');
      noteBtn?.addEventListener('click', async () => {
        try {
          const resp = await fetch(`/case-law/${item.id}/note`);
          const data = await resp.json().catch(()=>({}));
          if (!resp.ok || !data.ok) {
            throw new Error(data.msg || `HTTP ${resp.status}`);
          }
          const context = {
            kind: 'case-law',
            id: item.id,
            onSaved: (summary) => {
              if (previewEl) {
                previewEl.textContent = summary || 'No note saved yet.';
              }
            }
          };
          openNotesModal(data.content || '', data.content ? 'update' : 'create', context);
        } catch (err) {
          alert(`Unable to load note: ${err.message || err}`);
        }
      });

      resultsHost.append(card);
    });
  }

  function currentMode(){
    return document.querySelector('input[name="cls-mode"]:checked')?.value || 'name';
  }

  async function performSearch(){
    const mode = currentMode();
    const params = new URLSearchParams();

    if (mode === 'name') {
      const selectedRadio = document.querySelector('input[name="cls-name-mode"]:checked');
      const targetId = selectedRadio?.dataset.target;
      const input = targetId ? document.getElementById(targetId) : null;
      const party = input?.value.trim();
      if (!party) { alert('Enter a party name to search.'); return; }
      const modeSel = selectedRadio?.value || 'either';
      params.set('party', party);
      params.set('party_mode', modeSel);
    } else if (mode === 'citation') {
      const citation = citationInput?.value.trim();
      if (!citation) { alert('Enter a citation to search.'); return; }
      params.set('citation', citation);
    } else if (mode === 'year') {
      const year = yearSel?.value.trim();
      if (!year) { alert('Select a decision year.'); return; }
      params.set('year', year);
    } else if (mode === 'type') {
      const primary = primarySel?.value.trim();
      const caseType = caseTypeSel?.value.trim();
      if (!primary) { alert('Choose a primary type.'); return; }
      params.set('primary_type', primary);
      if (caseType) params.set('case_type', caseType);
    } else if (mode === 'advanced') {
      const text = textInput?.value.trim();
      if (!text) { alert('Enter a query for advanced search.'); return; }
      params.set('text', text);
    }

    params.set('limit', '200');

    try {
      const resp = await fetch(`/case-law/search?${params.toString()}`);
      const data = await resp.json().catch(()=>({}));
      if (!resp.ok) {
        throw new Error(data.error || `HTTP ${resp.status}`);
      }
      renderResults(data.results || []);
      if (data.filters) {
        applyFilters(data.filters);
      }
    } catch (err) {
      alert(`Search failed: ${err.message || err}`);
    }
  }

  $('#cls-search')?.addEventListener('click', performSearch);
  textInput?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      performSearch();
    }
  });

  $('#cls-reset')?.addEventListener('click', () => {
    activateMode(modeRadios[0]?.value || 'name');
    nameModeRadios.forEach((radio, idx) => { radio.checked = idx === 0; });
    nameTextInputs.forEach((input, idx) => {
      if (!input) return;
      input.value = '';
      input.disabled = idx !== 0;
    });
    updateNameInputs();
    if (citationInput) citationInput.value = '';
    if (textInput) textInput.value = '';
    if (yearSel) yearSel.selectedIndex = 0;
    if (primarySel) primarySel.selectedIndex = 0;
    if (caseTypeSel) {
      caseTypeSel.innerHTML = '<option value="">Case Type</option>';
      caseTypeSel.disabled = true;
    }
    if (resultsHost) {
      resultsHost.innerHTML = '<div class="result-item">Use the search tools above to view results.</div>';
    }
  });

  async function loadFilters(){
    try {
      const resp = await fetch('/case-law/search?limit=1');
      const data = await resp.json().catch(()=>({}));
      if (data.filters) {
        applyFilters(data.filters);
      }
    } catch (err) {
      console.warn('Failed to load case-law filters', err);
    }
  }

  loadFilters();
}

// -------------------- Notes modal global handlers --------------------
function bindGlobalNotesModalHandlers(){
  const modal   = document.getElementById('notesModal');
  const editor  = document.getElementById('notesEditor');
  const saveBtn = document.getElementById('saveNotesBtn');
  const cancel  = document.getElementById('cancelNotesBtn');
  const close   = document.getElementById('notesClose');
  const editBtn = document.getElementById('editNotesBtn');
  const title   = document.getElementById('notesTitle');

  if (!modal || !editor || !saveBtn || !cancel || !close || !editBtn) return;

  const NOTE_ESCAPE_RE = /\\r\\n|\\n|\\r/g;

  function asViewText(raw){
    if (!raw) return '';
    return raw.replace(NOTE_ESCAPE_RE, '\n');
  }

  let originalContent = '';
  let rawContent = '';
  let noteContext = null;

  const toggleVisibility = (el, shouldShow) => {
    if (!el) return;
    el.hidden = !shouldShow;
    el.classList.toggle('is-hidden', !shouldShow);
  };

  function updateDirtyState() {
    if (modal.dataset.state !== 'edit') {
      saveBtn.disabled = true;
      return;
    }
    saveBtn.disabled = editor.value === originalContent;
  }

  function setState(state){
    modal.dataset.state = state;
    const editing = state === 'edit';
    editor.readOnly = !editing;
    editor.classList.toggle('notes-readonly', !editing);
    toggleVisibility(saveBtn, editing);
    toggleVisibility(cancel, editing);
    toggleVisibility(editBtn, !(editing || modal.dataset.intent === 'create'));
    if (!editing) {
      editor.value = asViewText(rawContent);
      // ensure caret doesn't stay focused when in view mode
      editor.blur();
    } else {
      editor.value = rawContent;
    }
    updateDirtyState();
  }

  function openModal(content, intent){
    modal.dataset.intent = intent === 'create' ? 'create' : 'update';
    rawContent = content || '';
    originalContent = rawContent;
    setState(intent === 'create' ? 'edit' : 'view');
    editor.style.display = 'block';
    modal.removeAttribute('hidden');
    modal.setAttribute('aria-hidden','false');
    if (title) {
      title.textContent = intent === 'create' ? 'Create Note.json' : 'Case Notes (Note.json)';
    }
    if (modal.dataset.state === 'edit') {
      editor.focus();
      editor.setSelectionRange(editor.value.length, editor.value.length);
    }
  }

  function closeModal(){
    modal.setAttribute('hidden','');
    modal.setAttribute('aria-hidden','true');
    editor.readOnly = true;
    editor.blur();
    editor.style.display = 'none';
    noteContext = null;
  }

  // Public helper used by manageCaseForm
  window._openNotesWith = function(content, intent, context){
    noteContext = context || null;
    openModal(content || '', intent || 'update');
  };

  editBtn.addEventListener('click', () => {
    rawContent = originalContent;
    setState('edit');
    editor.focus();
    editor.setSelectionRange(editor.value.length, editor.value.length);
  });

  async function saveCurrent(){
    const intent = modal.dataset.intent === 'create' ? 'create' : 'update';
    const payloadContent = editor.value;

    if (noteContext && noteContext.kind === 'case-law') {
      const caseId = noteContext.id;
      if (!caseId) {
        alert('Missing case-law identifier.');
        return;
      }
      try {
        const resp = await fetch(`/case-law/${caseId}/note`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: payloadContent })
        });
        const data = await resp.json().catch(()=>({}));
        if (!resp.ok || !data.ok) {
          throw new Error(data.msg || `HTTP ${resp.status}`);
        }
        rawContent = editor.value;
        originalContent = rawContent;
        alert('Notes saved!');
        if (typeof noteContext.onSaved === 'function') {
          noteContext.onSaved(data.summary || '');
        }
        modal.dataset.intent = 'update';
        setState('view');
        closeModal();
      } catch (err) {
        alert(`Save failed: ${err.message || err}`);
      }
      return;
    }

    const yEl = document.getElementById('mc-year');
    const mEl = document.getElementById('mc-month');
    const cEl = document.getElementById('mc-case');
    const year  = (noteContext && noteContext.year) || yEl?.value || '';
    const month = (noteContext && noteContext.month) || mEl?.value || '';
    const cname = (noteContext && noteContext.caseName) || cEl?.value || '';

    if (!year || !month || !cname) {
      alert('Select Year, Month, and Case first.');
      return;
    }

    const body = { content: payloadContent };
    let resp;
    try {
      if (intent === 'create') {
        resp = await fetch('/api/create-note', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ year, month, case: cname, content: payloadContent })
        });
      } else {
        resp = await fetch(`/api/note/${year}/${month}/${encodeURIComponent(cname)}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
      }
      const data = await resp.json().catch(()=>({}));
      if (!resp.ok || !data.ok) {
        throw new Error(data.msg || `HTTP ${resp.status}`);
      }
      rawContent = editor.value;
      originalContent = rawContent;
      alert(intent === 'create' ? 'Note.json created!' : 'Notes saved!');
      modal.dataset.intent = 'update';
      setState('view');
      closeModal();
      if (typeof window.__refreshNoteButton === 'function') {
        window.__refreshNoteButton();
      }
    } catch (err) {
      alert(`Save failed: ${err.message || err}`);
    }
  }

  function handleCancel(){
    const editing = modal.dataset.state === 'edit';
    if (!editing) {
      rawContent = originalContent;
      setState('view');
      return;
    }
    rawContent = originalContent;
    if (modal.dataset.intent === 'create') {
      closeModal();
      setState('view');
      return;
    }
    setState('view');
  }

  toggleVisibility(saveBtn, false);
  toggleVisibility(cancel, false);
  toggleVisibility(editBtn, true);
  saveBtn.disabled = true;

  editor.addEventListener('input', updateDirtyState);

  saveBtn.addEventListener('click', saveCurrent);
  cancel.addEventListener('click', handleCancel);
  close.addEventListener('click', () => {
    rawContent = originalContent;
    closeModal();
    setState('view');
  });
}

// -------------------- Theme + flashes --------------------
function autoDismissFlashes(ms = 3000){
  const flashes = document.querySelectorAll('.flash-stack .flash');
  flashes.forEach(el => {
    // click to dismiss immediately
    const removeNow = () => { el.classList.add('flash-fade'); setTimeout(()=> el.remove(), 350); };
    el.addEventListener('click', removeNow, { once: true });

    // timed auto-dismiss
    setTimeout(() => {
      if (!document.body.contains(el)) return;
      el.classList.add('flash-fade');
      setTimeout(() => el.remove(), 350);
    }, ms);
  });
}

const THEME_KEY = 'caseOrg.theme';
function applyTheme(theme){
  document.documentElement.setAttribute('data-theme', theme);
  const btn = document.getElementById('theme-toggle');
  if (btn) {
    btn.innerHTML = theme === 'dark'
      ? '<i class="fa-solid fa-sun"></i>'
      : '<i class="fa-solid fa-moon"></i>';
  }
}

function initTheme(){
  const saved = localStorage.getItem(THEME_KEY);
  if (saved === 'light' || saved === 'dark') {
    applyTheme(saved);
    return;
  }
  const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  applyTheme(prefersDark ? 'dark' : 'light');
}

function setupThemeToggle(){
  const btn = document.getElementById('theme-toggle');
  if (!btn) return;
  btn.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme') || 'light';
    const next = current === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    localStorage.setItem(THEME_KEY, next);
  });

  const saved = localStorage.getItem(THEME_KEY);
  if (!saved && window.matchMedia) {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    mq.addEventListener('change', e => applyTheme(e.matches ? 'dark' : 'light'));
  }
}

// -------------------- Startup wiring (single DOMContentLoaded) --------------------
document.addEventListener('DOMContentLoaded', () => {
  // Flashes auto-dismiss
  autoDismissFlashes(3000);

  // Theme
  initTheme();
  setupThemeToggle();

  // Year dropdown in Advanced Search
  initYearDropdown('year-dd', 'year', 2025);

  // Simple search
  const searchBtn = $('#search-btn');
  const searchQ = $('#search-q');
  searchBtn?.addEventListener('click', runBasicSearch);
  searchQ?.addEventListener('keydown', (e)=>{ if (e.key === 'Enter') { e.preventDefault(); runBasicSearch(); }});

  // Advanced toggle
  const advToggle = $('#adv-toggle');
  const advForm = $('#adv-form');
  advToggle?.addEventListener('click', ()=>{
    const isHidden = advForm.hidden;
    advForm.hidden = !isHidden;
    advToggle.setAttribute('aria-expanded', String(!isHidden));
  });

  // Advanced domain -> subcat
  const advDom = $('#adv-domain');
  const advSub = $('#adv-subcat');
  advDom?.addEventListener('change', ()=>{
    const dom = advDom.value || '';
    if (dom && SUBCATS[dom]) {
      populateOptions(advSub, SUBCATS[dom], "Subcategory");
    } else if (advSub) {
      advSub.innerHTML = '<option value="">Subcategory</option>';
      advSub.disabled = true;
    }
  });

  // Advanced search run
  const advSearch = $('#adv-search');
  advSearch?.addEventListener('click', runAdvancedSearch);

  // Directory search (if button exists)
  const dirBtn = document.getElementById('dir-search');
  if (dirBtn) {
    dirBtn.setAttribute('aria-pressed', 'false');
    dirBtn.addEventListener('click', async () => {
      const results = document.getElementById('results');
      if (!results) return;

      if (!dirSearchState.active) {
        dirSearchState.active = true;
        dirSearchState.previousScroll = results.scrollTop || 0;
        dirSearchState.currentPath = '';
        dirBtn.classList.add('active');
        dirBtn.textContent = 'Exit Directory Search';
        dirBtn.setAttribute('aria-pressed', 'true');
        results.innerHTML = '<div class="result-item">Loading directory tree...</div>';
        await showDirLevel('');
      } else {
        dirSearchState.active = false;
        dirSearchState.currentPath = '';
        dirBtn.classList.remove('active');
        dirBtn.textContent = 'Directory Search';
        dirBtn.setAttribute('aria-pressed', 'false');
        const snapshot = cloneResults(lastRenderedResults) || null;
        if (Array.isArray(snapshot)) {
          renderResults(snapshot);
          const host = document.getElementById('results');
          if (host) host.scrollTop = dirSearchState.previousScroll || 0;
        } else {
          results.innerHTML = '<div class="result-item">Use the search tools above to view results.</div>';
        }
        dirSearchState.previousScroll = 0;
      }
    });
  }

  // Cards + forms
  const cardConfigs = [
    { el: $('#card-create'), handler: createCaseForm },
    { el: $('#card-manage'), handler: manageCaseForm },
    { el: $('#card-upload-case-law'), handler: caseLawUploadForm },
    { el: $('#card-search-case-law'), handler: caseLawSearchForm },
  ];

  const cardElements = cardConfigs.map(cfg => cfg.el).filter(Boolean);

  cardConfigs.forEach(({ el, handler }) => {
    if (!el || typeof handler !== 'function') return;
    const others = cardElements.filter(other => other !== el);
    const activate = () => {
      setActive(el, others);
      handler();
    };
    el.addEventListener('click', activate);
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        activate();
      }
    });
  });

  // Notes modal global handlers (Save/Cancel/Close)
  bindGlobalNotesModalHandlers();
});
