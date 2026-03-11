const TOKEN = 'CHANGE_ME_SECRET';

function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) {
      return jsonOut({ ok: false, error_code: 'EMPTY_BODY', error_message: 'Empty body' });
    }

    const body = JSON.parse(e.postData.contents);
    const tokenFromQuery = (e.parameter && e.parameter.token) || '';
    const tokenFromHeader = '';
    const tokenFromBody = String(body.token || '');
    const token = tokenFromBody || tokenFromQuery || tokenFromHeader;
    if (TOKEN && token !== TOKEN) {
      return jsonOut({ ok: false, error_code: 'UNAUTHORIZED', error_message: 'Unauthorized' });
    }

    const reportType = String(body.report_type || '');
    const payload = body.payload || {};
    if (reportType !== 'top10') {
      return jsonOut({ ok: false, error_code: 'UNSUPPORTED_REPORT_TYPE', error_message: 'Only report_type=top10 is supported' });
    }

    const schema = String(payload.export_schema_version || '');
    if (schema !== 'top10.v4.strict') {
      return jsonOut({
        ok: false,
        error_code: 'SCHEMA_MISMATCH',
        error_message: 'Expected export_schema_version=top10.v4.strict'
      });
    }

    const bundle = payload.export_bundle || {};
    const sheet1 = bundle.sheet1_matrix_rows;
    const sheet2 = bundle.sheet2_site_columns_rows;
    const sheet3 = bundle.sheet3_proposed_rows;

    const v1 = validateSheet1_(sheet1);
    if (!v1.ok) return jsonOut({ ok: false, error_code: 'SCHEMA_MISMATCH', error_message: v1.reason });
    const v2 = validateSheet2_(sheet2);
    if (!v2.ok) return jsonOut({ ok: false, error_code: 'SCHEMA_MISMATCH', error_message: v2.reason });
    const v3 = validateSheet3_(sheet3);
    if (!v3.ok) return jsonOut({ ok: false, error_code: 'SCHEMA_MISMATCH', error_message: v3.reason });

    const runId = String(payload.run_id || Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyyMMdd_HHmmss'));
    const ss = SpreadsheetApp.getActiveSpreadsheet();

    const compareSheetName = uniqueSheetName_(ss, 'run_' + runId + '_compare');
    const sitesSheetName = uniqueSheetName_(ss, 'run_' + runId + '_sites');
    const structureSheetName = uniqueSheetName_(ss, 'run_' + runId + '_structure');

    const compareSheet = ss.insertSheet(compareSheetName);
    const sitesSheet = ss.insertSheet(sitesSheetName);
    const structureSheet = ss.insertSheet(structureSheetName);

    writeRows_(compareSheet, sheet1);
    writeRows_(sitesSheet, sheet2);
    writeRows_(structureSheet, sheet3);

    return jsonOut({
      ok: true,
      spreadsheet_url: ss.getUrl(),
      compare_sheet: compareSheetName,
      sites_sheet: sitesSheetName,
      structure_sheet: structureSheetName
    });
  } catch (err) {
    return jsonOut({
      ok: false,
      error_code: 'INTERNAL_ERROR',
      error_message: String(err)
    });
  }
}

function validateSheet1_(rows) {
  if (!Array.isArray(rows) || rows.length < 2) return { ok: false, reason: 'sheet1_matrix_rows: expected at least 2 rows' };
  const header = rows[0];
  if (!Array.isArray(header) || header.length < 3) return { ok: false, reason: 'sheet1_matrix_rows: header must have at least 3 columns' };
  if (String(header[0] || '').trim() !== 'Блоки / Сайты') return { ok: false, reason: 'sheet1_matrix_rows: first header cell must be "Блоки / Сайты"' };
  const width = header.length;
  for (let i = 1; i < rows.length; i++) {
    const row = rows[i];
    if (!Array.isArray(row) || row.length !== width) return { ok: false, reason: 'sheet1_matrix_rows: row ' + (i + 1) + ' width mismatch' };
    for (let c = 1; c < row.length; c++) {
      const cell = String(row[c] || '').trim();
      if (!(cell === '' || cell === '✓')) return { ok: false, reason: 'sheet1_matrix_rows: invalid cell at row ' + (i + 1) };
    }
  }
  return { ok: true };
}

function validateSheet2_(rows) {
  if (!Array.isArray(rows) || rows.length < 2) return { ok: false, reason: 'sheet2_site_columns_rows: expected at least 2 rows' };
  const header = rows[0];
  if (!Array.isArray(header) || header.length < 2) return { ok: false, reason: 'sheet2_site_columns_rows: header must contain sites' };
  const width = header.length;
  for (let i = 0; i < header.length; i++) {
    if (!String(header[i] || '').trim()) return { ok: false, reason: 'sheet2_site_columns_rows: empty site in header' };
  }
  for (let i = 1; i < rows.length; i++) {
    const row = rows[i];
    if (!Array.isArray(row) || row.length !== width) return { ok: false, reason: 'sheet2_site_columns_rows: row ' + (i + 1) + ' width mismatch' };
  }
  return { ok: true };
}

function validateSheet3_(rows) {
  if (!Array.isArray(rows) || rows.length < 2) return { ok: false, reason: 'sheet3_proposed_rows: expected at least 2 rows' };
  const header = rows[0];
  if (!Array.isArray(header) || header.length < 3) return { ok: false, reason: 'sheet3_proposed_rows: expected 3-column header' };
  if (String(header[0] || '').trim() !== 'Блок (человекочитаемый)') return { ok: false, reason: 'sheet3_proposed_rows: invalid col1 header' };
  if (String(header[1] || '').trim() !== 'Блок (системный)') return { ok: false, reason: 'sheet3_proposed_rows: invalid col2 header' };
  if (String(header[2] || '').trim() !== 'Комментарии по блоку') return { ok: false, reason: 'sheet3_proposed_rows: invalid col3 header' };
  const width = header.length;
  for (let i = 1; i < rows.length; i++) {
    const row = rows[i];
    if (!Array.isArray(row) || row.length !== width) return { ok: false, reason: 'sheet3_proposed_rows: row ' + (i + 1) + ' width mismatch' };
  }
  return { ok: true };
}

function writeRows_(sheet, rows) {
  const width = rows[0].length;
  const padded = rows.map(function (row) {
    const next = row.slice(0);
    while (next.length < width) next.push('');
    return next;
  });
  sheet.getRange(1, 1, padded.length, width).setValues(padded);
  sheet.autoResizeColumns(1, width);
}

function uniqueSheetName_(ss, base) {
  let name = base.substring(0, 95);
  let i = 1;
  while (ss.getSheetByName(name)) {
    name = (base.substring(0, 90) + '_' + i).substring(0, 95);
    i++;
  }
  return name;
}

function jsonOut(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
