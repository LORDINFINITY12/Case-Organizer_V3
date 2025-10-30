document.addEventListener('DOMContentLoaded', () => {
  const invoiceBox = document.querySelector('.invoice-box');
  if (!invoiceBox) return;

  const downloadBtn = document.getElementById('invoice-download');
  const addItemBtn = document.getElementById('add-invoice-item');
  const itemsBody = document.getElementById('invoice-items');
  const totalDisplay = document.getElementById('invoice-total');
  const invoiceNumberField = invoiceBox.querySelector('[data-invoice-number]');
  const invoiceDateField = invoiceBox.querySelector('[data-invoice-date]');
  const contextEl = document.getElementById('invoice-context');
  const caseContext = {
    year: contextEl ? (contextEl.dataset.caseYear || '').trim() : '',
    month: contextEl ? (contextEl.dataset.caseMonth || '').trim() : '',
    caseName: contextEl ? (contextEl.dataset.caseName || '').trim() : '',
  };

  function textContent(node) {
    return (node?.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function extractLines(node, { ignoreHeadings = [] } = {}) {
    if (!node) return [];
    const headings = ignoreHeadings.map(h => h.toLowerCase());
    return node.innerText
      .split(/\r?\n/)
      .map(line => line.replace(/\s+/g, ' ').trim())
      .filter(line => {
        if (!line) return false;
        const lower = line.toLowerCase();
        return !headings.some(heading => lower === heading || lower.startsWith(`${heading}:`));
      });
  }

  function sanitizeForFilename(value) {
    return (value || '')
      .trim()
      .toLowerCase()
      .replace(/\s+/g, '-')
      .replace(/[^a-z0-9_-]+/g, '');
  }

  function normaliseAmount(value) {
    if (typeof value !== 'string') value = String(value ?? '');
    const cleaned = value.replace(/[^\d.]/g, '');
    if (!cleaned) return null;
    const number = parseFloat(cleaned);
    return Number.isFinite(number) ? number : null;
  }

  function formatAmountInput(value) {
    const number = normaliseAmount(value);
    if (number === null) return '';
    return number.toFixed(2);
  }

  function renumberRows() {
    itemsBody
      ?.querySelectorAll('tr.item')
      .forEach((row, index) => {
        const snCell = row.querySelector('.sn');
        if (snCell) snCell.textContent = String(index + 1);
      });
  }

  function updateTotals() {
    if (!itemsBody || !totalDisplay) return;
    let total = 0;
    itemsBody.querySelectorAll('.amount-field').forEach(field => {
      const value = normaliseAmount(field.textContent);
      if (value !== null) total += value;
    });
    totalDisplay.textContent = total.toFixed(2);
  }

  function createItemRow(values = {}) {
    const row = document.createElement('tr');
    row.className = 'item';

    const snCell = document.createElement('td');
    snCell.className = 'sn';
    row.appendChild(snCell);

    const itemCell = document.createElement('td');
    const itemField = document.createElement('span');
    itemField.className = 'invoice-field item-field';
    itemField.contentEditable = 'true';
    itemField.dataset.placeholder = 'Service or item';
    itemField.textContent = values.item || '';
    itemCell.appendChild(itemField);
    row.appendChild(itemCell);

    const descriptionCell = document.createElement('td');
    const descriptionField = document.createElement('div');
    descriptionField.className = 'invoice-field description-field';
    descriptionField.contentEditable = 'true';
    descriptionField.dataset.placeholder = 'Description';
    descriptionField.textContent = values.description || '';
    descriptionCell.appendChild(descriptionField);
    row.appendChild(descriptionCell);

    const amountCell = document.createElement('td');
    amountCell.className = 'amount-cell';
    const wrapper = document.createElement('div');
    wrapper.className = 'amount-wrapper';

    const amountField = document.createElement('span');
    amountField.className = 'invoice-field amount-field';
    amountField.contentEditable = 'true';
    amountField.dataset.placeholder = '0.00';
    amountField.textContent = values.amount ? formatAmountInput(values.amount) : '';

    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'invoice-remove';
    removeBtn.setAttribute('aria-label', 'Remove item');
    removeBtn.innerHTML = '&times;';

    wrapper.appendChild(amountField);
    wrapper.appendChild(removeBtn);
    amountCell.appendChild(wrapper);
    row.appendChild(amountCell);

    itemsBody?.appendChild(row);
    renumberRows();
    updateTotals();
  }

  function ensureAtLeastOneRow() {
    if (!itemsBody) return;
    if (!itemsBody.querySelector('tr.item')) {
      createItemRow();
    } else {
      renumberRows();
      updateTotals();
    }
  }

  ensureAtLeastOneRow();

  async function fetchNextInvoiceNumber() {
    try {
      const response = await fetch('/api/invoices/next-number', { credentials: 'same-origin' });
      if (!response.ok) return null;
      const data = await response.json().catch(() => null);
      if (data && typeof data.invoice_number === 'string') {
        return data.invoice_number;
      }
      return null;
    } catch (err) {
      console.warn('Unable to fetch next invoice number', err);
      return null;
    }
  }

  async function ensureInvoiceNumberPrefill() {
    if (!invoiceNumberField) return;
    const current = invoiceNumberField.textContent.trim();
    if (current && current !== '0001') return;
    const nextNumber = await fetchNextInvoiceNumber();
    if (nextNumber) {
      invoiceNumberField.textContent = nextNumber;
    }
  }

  ensureInvoiceNumberPrefill();

  addItemBtn?.addEventListener('click', () => {
    createItemRow();
    const lastRow = itemsBody?.lastElementChild;
    const firstField = lastRow?.querySelector('.item-field');
    firstField?.focus();
  });

  itemsBody?.addEventListener('input', event => {
    const target = event.target;

    if (target.matches('.amount-field')) {
      const clean = target.textContent.replace(/[^\d.]/g, '');
      const parts = clean.split('.');
      let integral = parts[0];
      let fractional = parts.slice(1).join('');
      if (fractional.length > 2) fractional = fractional.slice(0, 2);
      if (!integral && fractional) integral = '0';
      const normalised =
        fractional !== ''
          ? `${integral || '0'}.${fractional}`
          : integral;
      target.textContent = normalised;
      if (document.activeElement === target) {
        const selection = window.getSelection();
        if (selection) {
          const range = document.createRange();
          range.selectNodeContents(target);
          range.collapse(false);
          selection.removeAllRanges();
          selection.addRange(range);
        }
      }
      updateTotals();
    }
  });

  itemsBody?.addEventListener('blur', event => {
    const target = event.target;
    if (target.matches('.amount-field')) {
      target.textContent = formatAmountInput(target.textContent);
      updateTotals();
    } else if (target.matches('.item-field, .description-field')) {
      target.textContent = target.textContent.trim();
    }
  }, true);

  itemsBody?.addEventListener('click', event => {
    const removeBtn = event.target.closest('.invoice-remove');
    if (!removeBtn) return;
    const row = removeBtn.closest('tr.item');
    if (!row || !itemsBody) return;

    if (itemsBody.querySelectorAll('tr.item').length === 1) {
      row.querySelectorAll('.invoice-field').forEach(field => {
        field.textContent = '';
      });
    } else {
      row.remove();
    }
    renumberRows();
    updateTotals();
  });

  function collectInvoiceData() {
    const invoiceNumber = invoiceNumberField ? invoiceNumberField.textContent.trim() : '';
    const invoiceDate = invoiceDateField ? invoiceDateField.textContent.trim() : '';
    const clientName = textContent(invoiceBox.querySelector('[data-client-name]')) || 'client';
    const issuerLines = extractLines(invoiceBox.querySelector('[data-issuer-block]'), {
      ignoreHeadings: ['from'],
    });
    const recipientLines = extractLines(invoiceBox.querySelector('[data-recipient-block]'), {
      ignoreHeadings: ['to'],
    });

    const items = [];
    itemsBody?.querySelectorAll('tr.item').forEach((row, index) => {
      const itemField = row.querySelector('.item-field');
      const descriptionField = row.querySelector('.description-field');
      const amountField = row.querySelector('.amount-field');

      const item = itemField ? itemField.textContent.trim() : '';
      const description = descriptionField ? descriptionField.textContent.trim() : '';
      const amountValue = amountField ? amountField.textContent.trim() : '';
      const amountNumber = normaliseAmount(amountValue);

      if (item || description || amountNumber !== null) {
        items.push({
          sn: String(index + 1),
          item,
          description,
          amount: amountNumber !== null ? amountNumber.toFixed(2) : '',
        });
      }
    });

    const today = new Date();
    const stamp = today.toISOString().slice(0, 10);
    const identifier = invoiceNumber || 'invoice';
    const filenameParts = [identifier, clientName, invoiceDate || stamp]
      .map(sanitizeForFilename)
      .filter(Boolean);
    const suggestedFilename = `${filenameParts.join('_') || `invoice_${stamp}`}.pdf`;

    return {
      invoice_number: invoiceNumber,
      invoice_date: invoiceDate,
      client_name: clientName,
      issuer_lines: issuerLines,
      recipient_lines: recipientLines,
      items,
      total: totalDisplay ? totalDisplay.textContent.replace(/,/g, '') : '',
      generated_at: today.toISOString(),
      suggested_filename: suggestedFilename,
      case_year: caseContext.year,
      case_month: caseContext.month,
      case_name: caseContext.caseName,
    };
  }

  function parseFilenameFromDisposition(disposition) {
    if (!disposition) return null;
    const match = /filename\*?=(?:UTF-8'')?["']?([^"';]+)["']?/i.exec(disposition);
    return match ? decodeURIComponent(match[1]) : null;
  }

  async function handleDownload() {
    if (!downloadBtn) return;
    const payload = collectInvoiceData();
    const url = downloadBtn.dataset.downloadUrl || '/invoice/save';

    try {
      downloadBtn.disabled = true;
      downloadBtn.classList.add('is-busy');
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest',
        },
        credentials: 'same-origin',
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        let message = `Server responded with ${response.status}`;
        try {
          const data = await response.json();
          if (data && typeof data.msg === 'string' && data.msg) {
            message = data.msg;
          }
        } catch {
          const text = await response.text().catch(() => '');
          if (text) message = text;
        }
        throw new Error(message);
      }

      const blob = await response.blob();
      const disposition = response.headers.get('Content-Disposition');
      const finalNumber = response.headers.get('X-Invoice-Number');
      if (finalNumber && invoiceNumberField) {
        invoiceNumberField.textContent = finalNumber;
      }

      let filename = parseFilenameFromDisposition(disposition);
      if (!filename) {
        const fallbackNumber = finalNumber || payload.invoice_number || '';
        const parts = [
          fallbackNumber,
          caseContext.caseName || payload.client_name || 'client',
          payload.invoice_date || new Date().toISOString().slice(0, 10),
        ]
          .map(sanitizeForFilename)
          .filter(Boolean);
        const baseName = parts.join('_') || 'invoice';
        filename = `${baseName}.pdf`;
      }

      if (!filename.toLowerCase().endsWith('.pdf')) {
        filename += '.pdf';
      }

      const blobUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = blobUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(blobUrl), 10_000);
    } catch (err) {
      console.error('Invoice PDF download failed', err);
      let message = 'Unable to generate the PDF right now. Please try again or contact your administrator.';
      if (err && typeof err.message === 'string' && err.message) {
        message = err.message;
      }
      window.alert(message);
    } finally {
      downloadBtn.disabled = false;
      downloadBtn.classList.remove('is-busy');
    }
  }

  downloadBtn?.addEventListener('click', handleDownload);
});
