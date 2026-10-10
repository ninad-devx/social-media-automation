// Executes the workflow's Code nodes with n8n-like globals to prove the
// JavaScript parses AND behaves correctly. Run: node deploy/n8n/verify_workflow.js
const fs = require('fs');
const path = require('path');

const wfPath = path.join(__dirname, 'social-publishing-workflow.json');
const wf = JSON.parse(fs.readFileSync(wfPath, 'utf8'));

const node = (name) => wf.nodes.find((n) => n.name === name);
const jsOf = (name) => node(name).parameters.jsCode;

// n8n's Code node exposes these as globals.
function runCode(js, inputItems) {
  const $input = { all: () => inputItems };
  const $ = { json: inputItems[0] ? inputItems[0].json : {} };
  const fn = new Function('$input', '$', `${js}\n//# sourceURL=code-node`);
  return fn($input, $);
}

let failures = 0;
function check(label, condition, detail) {
  if (condition) {
    console.log(`  PASS  ${label}`);
  } else {
    failures++;
    console.log(`  FAIL  ${label}${detail ? ` -> ${detail}` : ''}`);
  }
}

const VALIDATE = jsOf('02 - Validate Request Contract');
const ERROR = jsOf('04 - Build Validation Error Response');
const NORMALIZE = jsOf('06 - Normalize Response');

console.log('\n[1] Code nodes parse and execute');
const goodBody = {
  body: {
    request_id: 'req_from_backend_123',
    account_id: 'sortpi',
    channel_id: 'ch_abc123def456',
    content: '  Hello from any brand  ',
    publish_mode: 'SCHEDULE',
    scheduled_at: '2026-01-01T10:00:00',
    timezone: 'Asia/Dhaka',
    media: [{ type: 'image', url: 'https://cdn.example.com/a.png', alt_text: 'alt' }],
  },
  headers: {},
};
let out = runCode(VALIDATE, [{ json: goodBody }]);
check('valid request accepted', out[0].json.valid === true, JSON.stringify(out[0].json.errors));
check('request id preserved from caller', out[0].json.request_id === 'req_from_backend_123');
check('content trimmed', out[0].json.payload.content === 'Hello from any brand');
check('media normalised', out[0].json.payload.media.length === 1);
check('alt_text kept', out[0].json.payload.media[0].alt_text === 'alt');

console.log('\n[2] Arbitrary new account/channel needs no workflow change');
const newBrand = JSON.parse(JSON.stringify(goodBody));
newBrand.body.account_id = 'brand_that_did_not_exist_yet';
newBrand.body.channel_id = 'ch_deadbeef9999';
out = runCode(VALIDATE, [{ json: newBrand }]);
check('unknown brand passes workflow validation', out[0].json.valid === true,
  'workflow must not hold an account registry');
check('new ids forwarded verbatim', out[0].json.payload.account_id === 'brand_that_did_not_exist_yet');

console.log('\n[3] Request id is generated when absent');
out = runCode(VALIDATE, [{ json: { body: { ...goodBody.body, request_id: undefined } } }]);
check('generated request id', /^[0-9A-HJKMNP-TV-Z]{26}$/.test(out[0].json.request_id),
  out[0].json.request_id);

console.log('\n[4] Invalid requests are rejected');
const bad = [
  [{ ...goodBody, body: { ...goodBody.body, account_id: '' } }, 'MISSING_ACCOUNT_ID'],
  [{ ...goodBody, body: { ...goodBody.body, channel_id: '   ' } }, 'MISSING_CHANNEL_ID'],
  [{ ...goodBody, body: { ...goodBody.body, content: '   ' } }, 'CONTENT_EMPTY'],
  [{ ...goodBody, body: { ...goodBody.body, publish_mode: 'LATER' } }, 'INVALID_PUBLISH_MODE'],
  [{ ...goodBody, body: { ...goodBody.body, publish_mode: 'SCHEDULE', scheduled_at: null } }, 'INVALID_SCHEDULE'],
  [{ ...goodBody, body: { ...goodBody.body, media: [{ type: 'gif', url: 'https://x/y' }] } }, 'MEDIA_INVALID'],
  [{ ...goodBody, body: { ...goodBody.body, media: [{ type: 'image', url: 'ftp://x' }] } }, 'MEDIA_INVALID'],
];
for (const [item, expected] of bad) {
  const r = runCode(VALIDATE, [{ json: item }]);
  const codes = (r[0].json.errors || []).map((e) => e.code);
  check(`rejects with ${expected}`, !r[0].json.valid && codes.includes(expected),
    JSON.stringify(codes));
}

console.log('\n[5] Validation error envelope');
const invalidOut = runCode(VALIDATE, [{ json: bad[0][0] }]);
const envelope = runCode(ERROR, [{ json: invalidOut[0].json }]);
check('envelope has success=false', envelope[0].json.success === false);
check('envelope carries the code', envelope[0].json.error.code === 'MISSING_ACCOUNT_ID');
check('envelope carries request_id', envelope[0].json.request_id === 'req_from_backend_123');

console.log('\n[6] Response normalization - success');
out = runCode(NORMALIZE, [{
  json: {
    statusCode: 200,
    body: {
      success: true, request_id: 'req_from_backend_123', status: 'scheduled',
      account_id: 'sortpi', account_name: 'SortPi',
      channel_id: 'ch_abc123def456', channel_name: 'Glaamora',
      platform: 'instagram', buffer_publication_id: 'post_1',
      external_link: 'https://instagram.com/p/1',
      scheduled_at: '2026-01-01T05:00:00+00:00', timezone: 'Asia/Dhaka',
      content_length: 22, media_count: 1,
    },
  },
}]);
check('success passthrough', out[0].json.success === true);
check('status preserved', out[0].json.status === 'scheduled');
check('publication id preserved', out[0].json.buffer_publication_id === 'post_1');
check('request id preserved', out[0].json.request_id === 'req_from_backend_123');

console.log('\n[7] Response normalization - failures');
const cases = [
  [{ statusCode: 401, body: { success: false, error: { code: 'X', message: 'no' } } }, 'X'],
  // A FastAPI-style error body is not our envelope, so the HTTP status decides.
  [{ statusCode: 502, body: { detail: 'nope' } }, 'BUFFER_SERVER_ERROR'],
  [{ statusCode: 429, body: {} }, 'BUFFER_RATE_LIMIT'],
  [{ statusCode: 503, body: {} }, 'BUFFER_SERVER_ERROR'],
  [{ statusCode: 0, body: '' }, 'BUFFER_NETWORK_ERROR'],
];
for (const [item, expected] of cases) {
  const r = runCode(NORMALIZE, [{ json: item }]);
  check(`normalizes to ${expected}`, r[0].json.success === false && r[0].json.error.code === expected,
    r[0].json.error && r[0].json.error.code);
}

console.log('\n[7b] Request id survives an error the backend did not shape');
out = runCode(NORMALIZE, [{
  json: { statusCode: 422, body: { detail: 'validation' }, headers: { 'x-request-id': 'req_from_backend_123' } },
}]);
check('request id recovered from header echo', out[0].json.request_id === 'req_from_backend_123',
  out[0].json.request_id);

console.log('\n[8] Backend unreachable hints at the right setting');
out = runCode(NORMALIZE, [{ json: { statusCode: 0, body: '' } }]);
check('mentions SMA_BACKEND_URL', out[0].json.error.message.includes('SMA_BACKEND_URL'),
  out[0].json.error.message);

console.log('\n[9] Unauthorized hints at the shared secret');
out = runCode(NORMALIZE, [{ json: { statusCode: 401, body: {} } }]);
check('mentions SMA_INTERNAL_SECRET', out[0].json.error.message.includes('SMA_INTERNAL_SECRET'),
  out[0].json.error.message);

console.log(`\n${failures === 0 ? 'ALL WORKFLOW CHECKS PASSED' : `${failures} CHECK(S) FAILED`}`);
process.exit(failures === 0 ? 0 : 1);
