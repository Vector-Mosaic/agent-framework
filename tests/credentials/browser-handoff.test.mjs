import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { spawn } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { createCredentialHandoff } from '../../repo_platform/credentials/BrowserHandoff.mjs';

const python = process.platform === 'win32' ? 'python' : 'python3';
const root = fileURLToPath(new URL('../../', import.meta.url));
const worker = fileURLToPath(new URL('../../repo_platform/credentials/worker.py', import.meta.url));
const safeMessage = 'The private credential handoff did not complete. Inspect the current browser state before retrying.';
const fakeValue = 'synthetic-browser-fixture-value';

function target(overrides = {}) {
  let inspections = 0;
  let urls = 0;
  const state = { filled: undefined, fills: 0 };
  const element = {
    ownerDocument: { location: { origin: 'https://example.test', href: 'https://example.test/sign-in' } },
    tagName: 'INPUT', id: 'password',
    getAttribute: name => ({ type: 'password', name: 'password', autocomplete: 'current-password' }[name] ?? null),
    hasAttribute: () => false,
    ...overrides.element,
  };
  return {
    state,
    tab: { url: async () => overrides.url?.(++urls) ?? 'https://example.test/sign-in' },
    field: {
      count: async () => overrides.count ?? 1,
      isVisible: async () => overrides.visible ?? true,
      isEnabled: async () => overrides.enabled ?? true,
      evaluate: async callback => {
        inspections++;
        overrides.inspect?.(inspections, element);
        return callback(element);
      },
      fill: async value => {
        state.fills++;
        if (overrides.fillError) throw new Error(`${value}: provider echoed input`);
        state.filled = value;
      },
    },
  };
}

function errorCode(code) {
  return error => {
    assert.equal(error.name, 'CredentialHandoffError');
    assert.equal(error.code, code);
    assert.equal(error.message, safeMessage);
    assert.equal(error.cause, undefined);
    assert.ok(!String(error.stack).includes(fakeValue));
    return true;
  };
}

// A missing executable proves these refusals happen before any custody worker.
const rejecting = createCredentialHandoff({ python: 'absent-browser-fixture-python' });
for (const [name, overrides] of [
  ['duplicate selection', { count: 2 }],
  ['hidden field', { visible: false }],
  ['disabled field', { enabled: false }],
  ['readonly field', { element: { hasAttribute: () => true } }],
  ['non-editable element', { element: { tagName: 'BUTTON' } }],
  ['file input', { element: { getAttribute: name => name === 'type' ? 'file' : null } }],
  ['insecure top-level page', { url: () => 'http://example.test/sign-in' }],
  ['userinfo in top-level URL', { url: () => 'https://user@example.test/sign-in' }],
  ['inconsistent frame origin', { element: { ownerDocument: { location: { origin: 'https://example.test', href: 'https://other.test/sign-in' } } } }],
]) {
  test(`refuses ${name} before custody access`, async () => {
    const selected = target(overrides);
    await assert.rejects(rejecting.fillCredential('fixture', 'password', selected), errorCode('target_rejected'));
    assert.equal(selected.state.fills, 0);
  });
}

test('configuration accepts a command only and an absolute nonsecret registry path', () => {
  assert.throws(() => createCredentialHandoff({ python: 'python\nother' }), errorCode('configuration_rejected'));
  assert.throws(() => createCredentialHandoff({ config: './registry.json' }), errorCode('configuration_rejected'));
});

test('selection rejects unsupported credential types before custody access', async () => {
  await assert.rejects(rejecting.fillCredential('fixture', 'raw_totp_seed', target()), errorCode('selection_rejected'));
});

test('module imports without ambient process or Buffer globals in a restricted browser runtime', async () => {
  const moduleUrl = new URL('../../repo_platform/credentials/BrowserHandoff.mjs', import.meta.url).href;
  await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ['--input-type=module', '-e',
      `const url=${JSON.stringify(moduleUrl)}; globalThis.process=undefined; globalThis.Buffer=undefined; const m=await import(url); if(typeof m.createCredentialHandoff !== 'function') throw new Error('Missing API');`],
    { windowsHide: true, stdio: ['ignore', 'ignore', 'ignore'] });
    child.once('error', reject);
    child.once('close', code => code === 0 ? resolve() : reject(new Error('Restricted-runtime import failed')));
  });
});

function workerCall(request, extraPipe = true) {
  return new Promise((resolve, reject) => {
    const child = spawn(python, [worker], {
      windowsHide: true,
      stdio: extraPipe ? ['pipe', 'pipe', 'pipe', 'pipe'] : ['pipe', 'pipe', 'pipe'],
    });
    const stdout = [], stderr = [], privateChunks = [];
    child.once('error', reject);
    child.stdout.on('data', chunk => stdout.push(chunk));
    child.stderr.on('data', chunk => stderr.push(chunk));
    child.stdio[3]?.on('data', chunk => privateChunks.push(chunk));
    child.once('close', code => resolve({
      code,
      result: JSON.parse(Buffer.concat(stdout).toString('utf8')),
      stderr: Buffer.concat(stderr).toString('utf8'),
      privateBytes: Buffer.concat(privateChunks),
    }));
    child.stdin.end(JSON.stringify(request));
  });
}

test('worker accepts the inherited local channel before rejecting invalid request', async () => {
  const result = await workerCall({ operation: 'invalid' });
  assert.equal(result.code, 2);
  assert.deepEqual(result.result, { status: 'error', code: 'request_invalid' });
  assert.equal(result.stderr, '');
  assert.equal(result.privateBytes.length, 0);
});

test('worker refuses execution without a private output channel', async () => {
  const result = await workerCall({}, false);
  assert.equal(result.code, 2);
  assert.deepEqual(result.result, { status: 'error', code: 'private_pipe_required' });
  assert.equal(result.stderr, '');
});

// Exercise real CurrentUser DPAPI and worker transport with synthetic custody.
// Linux protected-store integration is qualified separately with an unlocked
// Secret Service session; these tests never introduce a plaintext store fallback.
let fixture;
let registry;
let handoff;
let previousState;
before(async () => {
  if (process.platform !== 'win32') return;
  fixture = await mkdtemp(join(tmpdir(), 'framework-browser-test-'));
  registry = join(fixture, 'accounts.json');
  await writeFile(registry, JSON.stringify({
    version: 1,
    namespace: randomUUID(),
    accounts: [{ id: 'fixture', status: 'active', allowed_origins: ['https://example.test'], identity: {}, credentials: { password: randomUUID() } }],
  }));
  previousState = process.env.AGENT_FRAMEWORK_STATE_HOME;
  process.env.AGENT_FRAMEWORK_STATE_HOME = join(fixture, 'state');
  await new Promise((resolve, reject) => {
    const child = spawn(python, ['-', registry], { cwd: root, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    const stderr = [];
    child.stderr.on('data', chunk => stderr.push(chunk));
    child.stdout.resume();
    child.once('error', reject);
    child.once('close', code => code === 0 ? resolve() : reject(new Error(`Synthetic custody setup failed: ${Buffer.concat(stderr).toString('utf8')}`)));
    child.stdin.end(`from pathlib import Path\nimport sys\nfrom repo_platform.credentials.custody import resolve_selection, store_for, write_payload\ns = resolve_selection('fixture', 'password', 'https://example.test', config=Path(sys.argv[1]))\nwith store_for(s) as store:\n    write_payload(s, '${fakeValue}', store)\n`);
  });
  handoff = createCredentialHandoff({ python, config: registry });
});

after(async () => {
  if (process.platform !== 'win32') return;
  if (previousState === undefined) delete process.env.AGENT_FRAMEWORK_STATE_HOME;
  else process.env.AGENT_FRAMEWORK_STATE_HOME = previousState;
  // Exact test-created directory, never an existing user credential directory.
  if (fixture) await rm(fixture, { recursive: true, force: true });
});

const protectedStoreOptions = { skip: process.platform !== 'win32' ? 'Requires Windows DPAPI; Linux custody is tested with Secret Service separately.' : false };

test('fills one observed field through the real private worker and returns metadata only', protectedStoreOptions, async () => {
  const selected = target();
  const result = await handoff.fillCredential('fixture', 'password', selected);
  assert.equal(selected.state.filled, fakeValue);
  assert.deepEqual(result, { status: 'filled', account: 'fixture', credential: 'password', origin: 'https://example.test' });
  assert.ok(!JSON.stringify(result).includes(fakeValue));
});

test('refuses a same-origin URL change after custody access', protectedStoreOptions, async () => {
  const selected = target({ url: count => `https://example.test/sign-in?attempt=${count}` });
  await assert.rejects(handoff.fillCredential('fixture', 'password', selected), errorCode('target_changed'));
  assert.equal(selected.state.fills, 0);
});

test('refuses changed field identity metadata after custody access', protectedStoreOptions, async () => {
  const selected = target({ inspect: (count, element) => { if (count === 2) element.id = 'different-field'; } });
  await assert.rejects(handoff.fillCredential('fixture', 'password', selected), errorCode('target_changed'));
  assert.equal(selected.state.fills, 0);
});

test('independently rejects an unadmitted frame even below an admitted page', protectedStoreOptions, async () => {
  const selected = target({ element: { ownerDocument: { location: { origin: 'https://other.test', href: 'https://other.test/sign-in' } } } });
  await assert.rejects(handoff.fillCredential('fixture', 'password', selected), errorCode('origin_not_allowed'));
  assert.equal(selected.state.fills, 0);
});

test('never exposes browser errors containing an attempted input', protectedStoreOptions, async () => {
  const selected = target({ fillError: true });
  await assert.rejects(handoff.fillCredential('fixture', 'password', selected), errorCode('browser_fill_uncertain'));
  assert.equal(selected.state.fills, 1);
});
