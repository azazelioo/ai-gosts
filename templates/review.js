const reviewPane = document.querySelector('#document-review');
const supportsReview = {{ (request.app.routes | selectattr('name', 'equalto', 'paragraph_fix') | list | length > 0) | tojson }};
const saveReview = document.querySelector('#save-review');
const reviewText = document.querySelector('#review-text');
const reviewStatus = document.querySelector('#review-feedback');
let reviewFile, reviewBusy = false;
let paragraphsByIndex = new Map();
let mathIndices = new Set();
const applied = new Set();

function resetReview() {
    const keepDownload = reviewFile === state.documentFile && !saveReview.hidden;
    reviewFile = state.documentFile;
    applied.clear();
    saveReview.hidden = !keepDownload;
    reviewStatus.textContent = '';
    const extra = state.report.parsed_document?.meta?.extras || {};
    paragraphsByIndex = new Map([...(extra.docx_paragraphs || []), ...(extra.docx_table_paragraphs || [])]
        .map(p => [p.paragraph_index, p]));
    mathIndices = new Set((extra.docx_formulas || []).filter(p => p.has_math_xml).map(p => p.paragraph_index));
    elements.downloadFixedButton.hidden = supportsReview && extra.source_format === 'docx';
}

function targetIndex(issue) {
    const doc = state.report.parsed_document;
    const loc = issue.location || {};
    if (loc.paragraph_index != null) return loc.paragraph_index;
    if (loc.paragraph_id) {
        const p = doc.paragraphs.find(item => item.id === loc.paragraph_id);
        if (p) return p.position.paragraph_index;
    }
    if (loc.section_id && doc.sections.filter(s => s.id === loc.section_id).length === 1)
        return doc.meta.extras.section_headings?.[loc.section_id]?.paragraph_index;
}

function canApplyHere(issue) {
    const index = issue.location?.paragraph_index;
    const p = paragraphsByIndex.get(index);
    if (!p || p.table_index || applied.has(issue.id)) return false;
    if (issue.subtype === 'enumeration_indent_invalid')
        return /^(?:[-–—]|[0-9]+\)|[а-я]\))\s+/iu.test(p.text);
    return issue.subtype === 'formula_not_centered' && mathIndices.has(index) && /^[\s.,;:()\d]*$/.test(p.text);
}

// PDF page numbers refer to the locally bundled edition of ГОСТ 7.32-2017.
const normClauses = {
    enumeration_indent_invalid: ['6.4.6', 13], invalid_enumeration_marker: ['6.4.6', 13],
    invalid_first_line_indent: ['6.1.1', 11], structural_heading_not_centered: ['6.2.1', 12],
    main_heading_indent_invalid: ['6.2.3', 12], main_heading_centered: ['6.2.3', 12],
    formula_missing_blank_lines: ['6.8.1', 18], formula_not_centered: ['6.8.3', 18],
    missing_formula_number: ['6.8.3', 18], missing_formula_reference: ['6.8.4', 18],
    missing_section_number: ['6.4.1', 13], numbering_error: ['6.4.1', 13],
    order_error: ['6.4.1', 13],
};
function normLink(issue) {
    const source = issue.standard_reference?.source;
    if (!supportsReview || state.report.standard_id !== 'gost_7_32_2017' || source !== 'ГОСТ 7.32-2017')
        return escapeHtml(source || 'Источник не установлен');
    const clause = normClauses[issue.subtype];
    const page = clause ? '#page=' + clause[1] : '';
    const label = clause ? source + ', п. ' + clause[0] : source + ' (пункт требует сверки)';
    return '<a target="_blank" rel="noopener" href="/review/standards/gost_7_32_2017.pdf' + page + '">' + escapeHtml(label) + '</a>';
}
function reviewNote(i) {
    const button = canApplyHere(i) && !state.dismissedIssueIds.has(i.id)
        ? '<button type="button" data-apply="' + escapeHtml(i.id) + '">Исправить в копии</button>' : '';
    return '<div class="review-note"><strong>' + escapeHtml(i.message) + '</strong><p>' +
        formatSuggestion(i.suggestion) + '</p>' + normLink(i) + ' ' + button + '</div>';
}

function renderDocumentReview(issues) {
    reviewPane.hidden = !supportsReview || state.report?.parsed_document?.meta?.extras?.source_format !== 'docx';
    if (reviewPane.hidden) return;
    const groups = new Map(), general = [];
    for (const i of issues) {
        const index = targetIndex(i);
        if (!paragraphsByIndex.has(index)) { general.push(i); continue; }
        if (!groups.has(index)) groups.set(index, []);
        groups.get(index).push(i);
    }
    const rows = [];
    for (const [index, p] of paragraphsByIndex) {
        const notes = groups.get(index);
        const text = escapeHtml(p.text);
        const label = p.table_index ? 'Таблица ' + p.table_index + ', строка ' + p.row_index + ', ячейка ' + p.cell_index + ' · ' : '';
        const indent = Number.isFinite(p.first_line_indent_mm) ? p.first_line_indent_mm : 0;
        const align = ['center', 'right', 'justify'].includes(p.alignment) ? p.alignment : 'left';
        const style = ' style="text-indent:' + indent + 'mm;text-align:' + align + '"';
        if (!notes) { rows.push('<p' + style + '>' + label + text + '</p>'); continue; }
        rows.push('<details class="review-anchor" data-index="' + index + '"><summary' + style + '>' + label + text +
            ' <small>(' + notes.length + ')</small></summary>' + notes.map(reviewNote).join('') + '</details>');
    }
    if (general.length) rows.push('<h4>Замечания без точной привязки</h4>' + general.map(reviewNote).join(''));
    reviewText.innerHTML = rows.join('');
}
reviewText.addEventListener('pointerover', event => {
    if (event.pointerType !== 'mouse') return;
    const row = event.target.closest('.review-anchor');
    if (row && !row.contains(event.relatedTarget)) row.open = true;
});
reviewText.addEventListener('pointerout', event => {
    const row = event.target.closest('.review-anchor');
    if (event.pointerType === 'mouse' && row && !row.contains(event.relatedTarget) && !row.contains(document.activeElement)) row.open = false;
});
reviewText.addEventListener('click', async event => {
    const btn = event.target.closest('[data-apply]');
    if (!btn || reviewBusy) return;
    const issue = getActiveIssues().find(i => i.id === btn.dataset.apply);
    if (!issue || !canApplyHere(issue)) return;
    const index = issue.location.paragraph_index;
    const data = new FormData();
    data.append('document', reviewFile);
    data.append('paragraph_index', index);
    data.append('expected_text', paragraphsByIndex.get(index).text);
    data.append('subtype', issue.subtype);
    reviewBusy = true;
    btn.disabled = elements.runButton.disabled = elements.documentInput.disabled = true;
    try {
        const response = await fetch('/review/paragraph-fix', {method: 'POST', body: data});
        if (!response.ok) throw Error((await readApiResponse(response)).detail || 'Ошибка исправления');
        const blob = await response.blob();
        reviewFile = new File([blob], state.documentFile.name, {type: blob.type});
        state.documentFile = reviewFile;
        const paragraph = paragraphsByIndex.get(index);
        if (issue.subtype === 'enumeration_indent_invalid') paragraph.first_line_indent_mm = 12.5;
        if (issue.subtype === 'formula_not_centered') paragraph.alignment = 'center';
        applied.add(issue.id);
        saveReview.hidden = false;
        reviewStatus.textContent = 'Исправление применено к рабочей копии. Можно продолжить проверку или скачать DOCX.';
        renderReport();
    } catch (err) { reviewStatus.textContent = err.message; }
    finally { reviewBusy = btn.disabled = elements.runButton.disabled = elements.documentInput.disabled = false; }
});
saveReview.addEventListener('click', () => {
    const url = URL.createObjectURL(reviewFile);
    const link = document.createElement('a');
    link.href = url;
    link.download = reviewFile.name.replace(/\.docx$/i, '') + '_исправлено.docx';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
});
