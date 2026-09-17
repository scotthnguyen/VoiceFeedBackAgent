/**
 * Google Apps Script — feedback sink for the drive-thru prototype.
 *
 * No Google Cloud project, service account, or key needed. This runs *inside*
 * your Google Sheet and exposes a URL our Python posts feedback rows to.
 *
 * SETUP (one time):
 *   1. Open your Google Sheet → Extensions → Apps Script.
 *   2. Delete any starter code, paste THIS whole file, and Save.
 *   3. (Optional) set a shared secret below and put the same value in .env as
 *      SHEET_WEBHOOK_TOKEN. Leave "" to disable the check.
 *   4. Click Deploy → New deployment → type "Web app".
 *        - Execute as: Me
 *        - Who has access: Anyone
 *      Deploy, authorize when prompted, and COPY the Web app URL.
 *   5. Put that URL in .env as SHEET_WEBHOOK_URL.
 */

var SHARED_TOKEN = ""; // must match SHEET_WEBHOOK_TOKEN in .env, or "" to skip

function doPost(e) {
  try {
    var body = JSON.parse(e.postData.contents);

    if (SHARED_TOKEN && body.token !== SHARED_TOKEN) {
      return _json({ ok: false, error: "unauthorized" });
    }

    var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];

    // Write the header row once, if headers were provided and the sheet is empty.
    if (sheet.getLastRow() === 0 && body.headers) {
      sheet.appendRow(body.headers);
      _applyFormatting(sheet);
    }
    sheet.appendRow(body.row);

    return _json({ ok: true });
  } catch (err) {
    return _json({ ok: false, error: String(err) });
  }
}

function _json(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

/**
 * Run this ONCE from the editor (pick "formatSheet" in the toolbar dropdown → Run)
 * to style an existing sheet. New sheets get formatted automatically on first write.
 */
function formatSheet() {
  _applyFormatting(SpreadsheetApp.getActiveSpreadsheet().getSheets()[0]);
}

function _applyFormatting(sheet) {
  var lastCol = sheet.getLastColumn() || 11;
  var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
  var maxRows = sheet.getMaxRows();

  // Header row: bold white text on brand red, frozen so it stays visible.
  sheet.getRange(1, 1, 1, lastCol)
    .setFontWeight('bold')
    .setFontColor('#ffffff')
    .setBackground('#d62828')
    .setVerticalAlignment('middle');
  sheet.setFrozenRows(1);
  sheet.setRowHeight(1, 34);

  // Sensible column widths by header name.
  var widths = {
    timestamp_utc: 150, local_time: 175, store_id: 75, brand: 130,
    store_name: 190, region: 100, rating: 70,
    feedback_transcript: 320, summary: 300, issue_tag: 145, sentiment: 100
  };
  headers.forEach(function (name, i) {
    if (widths[name]) sheet.setColumnWidth(i + 1, widths[name]);
  });

  // Wrap the long free-text columns.
  ['feedback_transcript', 'summary'].forEach(function (name) {
    var idx = headers.indexOf(name);
    if (idx >= 0) sheet.getRange(2, idx + 1, maxRows - 1, 1).setWrap(true);
  });

  // Color-code rating and sentiment.
  var rules = [];
  var ratingIdx = headers.indexOf('rating');
  if (ratingIdx >= 0) {
    var r = sheet.getRange(2, ratingIdx + 1, maxRows - 1, 1);
    rules.push(SpreadsheetApp.newConditionalFormatRule().whenNumberLessThan(4)
      .setBackground('#f4c7c3').setRanges([r]).build());
    rules.push(SpreadsheetApp.newConditionalFormatRule().whenNumberBetween(4, 6)
      .setBackground('#fce8b2').setRanges([r]).build());
    rules.push(SpreadsheetApp.newConditionalFormatRule().whenNumberGreaterThanOrEqualTo(7)
      .setBackground('#b7e1cd').setRanges([r]).build());
  }
  var sentimentIdx = headers.indexOf('sentiment');
  if (sentimentIdx >= 0) {
    var s = sheet.getRange(2, sentimentIdx + 1, maxRows - 1, 1);
    rules.push(SpreadsheetApp.newConditionalFormatRule().whenTextEqualTo('negative')
      .setBackground('#f4c7c3').setRanges([s]).build());
    rules.push(SpreadsheetApp.newConditionalFormatRule().whenTextEqualTo('positive')
      .setBackground('#b7e1cd').setRanges([s]).build());
  }
  sheet.setConditionalFormatRules(rules);
}
