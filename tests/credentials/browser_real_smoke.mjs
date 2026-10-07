/** Real-browser qualification with synthetic credentials and local route fulfillment.
 * No network provider sign-in, personal account, browser profile or live secret is used.
 * Run under an unlocked disposable Secret Service session on Linux.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { dirname, isAbsolute, join } from 'node:path';
import { randomUUID, createHmac } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { createCredentialHandoff } from '../../repo_platform/credentials/BrowserHandoff.mjs';

const options = {};
for (let i = 2; i < process.argv.length; i += 2) {
  const name = process.argv[i];
  if (!['--config', '--python', '--engine'].includes(name) || !process.argv[i + 1]) throw new Error('Invalid qualification arguments');
  options[name.slice(2)] = process.argv[i + 1];
}
if (!options.config || !isAbsolute(options.config) || !options.python) throw new Error('An absolute disposable --config and --python executable are required');
const engineName = options.engine ?? 'firefox';
if (!['firefox', 'chromium', 'webkit'].includes(engineName)) throw new Error('Unsupported browser engine');
const root = fileURLToPath(new URL('../../', import.meta.url));
const password = 'synthetic-browser-fixture-value';
const seed = 'JBSWY3DPEHPK3PXP';
const rawSeed = Buffer.from('Hello!\xde\xad\xbe\xef', 'latin1');
let browser;
let state;
let registryCreated = false;
let phase = 'setup';
let checks = 0;
const oldState = process.env.AGENT_FRAMEWORK_STATE_HOME;
const fixtureIdentity = { namespace: randomUUID(), password: randomUUID(), totp: randomUUID() };
const privateScript = script => new Promise((resolve, reject) => {
  const child = spawn(options.python, ['-', options.config], { cwd: root, windowsHide: true, stdio: ['pipe', 'ignore', 'ignore'] });
  child.once('error', () => reject(new Error('Private fixture operation failed')));
  child.once('close', code => code === 0 ? resolve() : reject(new Error('Private fixture operation failed')));
  child.stdin.end(script);
});
// The native-browser contract uses timeoutMs; standard Playwright uses timeout.
const browserField = locator => ({
  count: () => locator.count(),
  isVisible: () => locator.isVisible(),
  isEnabled: () => locator.isEnabled(),
  evaluate: callback => locator.evaluate(callback),
  fill: (value, { timeoutMs }) => locator.fill(value, { timeout: timeoutMs }),
});
const codeAt = seconds => {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(seconds / 30)));
  const digest = createHmac('sha1', rawSeed).update(counter).digest();
  const offset = digest.at(-1) & 15;
  return String((digest.readUInt32BE(offset) & 0x7fffffff) % 1000000).padStart(6, '0');
};
const fieldHtml = '<input id="password" type="password" name="password"><input id="otp" type="text"><code id="seed">JBSWY3DPEHPK3PXP</code><button id="accepted" onclick="this.dataset.accepted=\'true\'">Accept synthetic enrollment</button>';
try {
  await mkdir(dirname(options.config), { recursive: true, mode: 0o700 });
  state = await mkdtemp(join(dirname(options.config), 'browser-custody-'));
  process.env.AGENT_FRAMEWORK_STATE_HOME = state;
  await writeFile(options.config, JSON.stringify({
    version: 1, namespace: fixtureIdentity.namespace,
    accounts: [{ id: 'fixture', status: 'active', allowed_origins: ['https://example.test'], identity: {},
      credentials: { password: fixtureIdentity.password, totp: fixtureIdentity.totp } }],
  }), { flag: 'wx', mode: 0o600 });
  registryCreated = true;
  await privateScript(`from pathlib import Path\nimport sys\nfrom repo_platform.credentials.custody import resolve_selection, store_for, write_payload\ns = resolve_selection('fixture', 'password', 'https://example.test', config=Path(sys.argv[1]))\nwith store_for(s) as store:\n    write_payload(s, '${password}', store)\n`);
  const engines = await import(process.env.PLAYWRIGHT_MODULE ?? 'playwright');
  browser = await engines[engineName].launch({ headless: true });
  const page = await browser.newPage();
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    const body = url.pathname === '/outer'
      ? '<iframe id="allowed" src="https://example.test/inner"></iframe>'
      : url.pathname === '/login'
        ? `${fieldHtml}<iframe id="denied" src="https://unapproved.test/inner"></iframe>`
        : fieldHtml;
    return route.fulfill({ status: 200, contentType: 'text/html', body });
  });
  const handoff = createCredentialHandoff({ python: options.python, config: options.config });
  phase = 'password';
  await page.goto('https://example.test/login');
  const result = await handoff.fillCredential('fixture', 'password', { tab: page, field: browserField(page.locator('#password')) });
  assert.deepEqual(result, { status: 'filled', account: 'fixture', credential: 'password', origin: 'https://example.test' });
  assert.equal(await page.locator('#password').inputValue(), password);
  checks++;
  phase = 'duplicate-field';
  await assert.rejects(handoff.fillCredential('fixture', 'password', { tab: page, field: browserField(page.locator('input')) }), error => error.code === 'target_rejected');
  checks++;
  phase = 'unadmitted-frame';
  await assert.rejects(handoff.fillCredential('fixture', 'password', { tab: page, field: browserField(page.frameLocator('#denied').locator('#password')) }), error => error.code === 'origin_not_allowed');
  checks++;
  phase = 'unadmitted-outer-page';
  await page.goto('https://unapproved.test/outer');
  await assert.rejects(handoff.fillCredential('fixture', 'password', { tab: page, field: browserField(page.frameLocator('#allowed').locator('#password')) }), error => error.code === 'origin_not_allowed');
  checks++;
  phase = 'totp-pending';
  await page.goto('https://example.test/login');
  assert.equal((await handoff.totpStatus('fixture', { tab: page })).state, 'not_configured');
  assert.equal((await handoff.captureTotpEnrollment('fixture', { tab: page, secretField: browserField(page.locator('#seed')) })).state, 'pending');
  await assert.rejects(handoff.fillCredential('fixture', 'totp_code', { tab: page, field: browserField(page.locator('#otp')) }));
  checks++;
  phase = 'totp-active';
  await page.locator('#accepted').click();
  assert.equal(await page.locator('#accepted').getAttribute('data-accepted'), 'true');
  assert.equal((await handoff.confirmTotpEnrollment('fixture', { tab: page })).state, 'active');
  const start = Date.now() / 1000;
  await handoff.fillCredential('fixture', 'totp_code', { tab: page, field: browserField(page.locator('#otp')) });
  const received = await page.locator('#otp').inputValue();
  assert.ok([codeAt(start), codeAt(Date.now() / 1000)].includes(received));
  checks++;
  phase = 'password-preserved';
  await handoff.fillCredential('fixture', 'password', { tab: page, field: browserField(page.locator('#password')) });
  assert.equal(await page.locator('#password').inputValue(), password);
  checks++;
} catch {
  // Browser exceptions can echo attempted input; emit no raw exception or stack.
  console.error(JSON.stringify({ status: 'failed', phase, checks }));
  process.exitCode = 1;
} finally {
  try { await browser?.close(); } catch {
    console.error(JSON.stringify({ status: 'failed', phase: 'browser-close', checks }));
    process.exitCode = 1;
  }
  let custodyCleaned = !registryCreated;
  if (registryCreated) {
    try {
      // Delete only the two fresh synthetic references under the same custody
      // environment. Preserve the registry and state if cleanup is uncertain.
      await privateScript(`from pathlib import Path\nimport json,sys\nfrom repo_platform.credentials.custody import resolve_custody_selection, store_for\nexpected=json.loads('${JSON.stringify(fixtureIdentity)}')\nfor purpose in ('password','totp'):\n    s=resolve_custody_selection('fixture',purpose,config=Path(sys.argv[1]))\n    if s._registry['namespace'] != expected['namespace'] or s.reference != expected[purpose]:\n        raise RuntimeError('Fixture binding changed')\n    with store_for(s) as store:\n        store.delete()\n        if store.read() is not None:\n            raise RuntimeError('Fixture cleanup incomplete')\n`);
      custodyCleaned = true;
    } catch {
      console.error(JSON.stringify({ status: 'failed', phase: 'custody-cleanup', checks, fixture_retained: true }));
      process.exitCode = 1;
    }
  }
  if (oldState === undefined) delete process.env.AGENT_FRAMEWORK_STATE_HOME;
  else process.env.AGENT_FRAMEWORK_STATE_HOME = oldState;
  if (custodyCleaned) {
    if (registryCreated) await rm(options.config, { force: true });
    if (state) await rm(state, { recursive: true, force: true });
  }
  if (!process.exitCode) console.log(JSON.stringify({ status: 'passed', engine: engineName, checks, cleanup: 'complete', scope: 'synthetic protected-custody real-browser integration' }));
}
