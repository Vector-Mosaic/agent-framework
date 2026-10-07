import { spawn } from 'node:child_process';
import { fileURLToPath, URL } from 'node:url';
import { TextDecoder } from 'node:util';
import { isAbsolute } from 'node:path';
import { platform } from 'node:os';
import { Buffer } from 'node:buffer';
import { setTimeout, clearTimeout } from 'node:timers';

const worker = fileURLToPath(new URL('./worker.py', import.meta.url));
const maximumBytes = 16384;
const timeoutMs = 15000;
const safeMessage = 'The private credential handoff did not complete. Inspect the current browser state before retrying.';

class HandoffError extends Error {
  constructor(code) {
    super(safeMessage);
    this.name = 'CredentialHandoffError';
    this.code = code;
  }
}

function fail(code) { return new HandoffError(code); }

async function inspectTarget({ tab, field }) {
  const url = new URL(await tab.url());
  if (url.protocol !== 'https:' || url.username || url.password) throw fail('target_rejected');
  if (await field.count() !== 1 || !await field.isVisible() || !await field.isEnabled()) {
    throw fail('target_rejected');
  }
  // Read only target metadata. Both the page and receiving frame must be
  // admitted by the selected account; an allowed page does not admit its frames.
  const metadata = await field.evaluate(element => ({
    origin: element.ownerDocument.location.origin,
    documentUrl: element.ownerDocument.location.href,
    tag: element.tagName,
    type: element.getAttribute('type'),
    readonly: element.hasAttribute('readonly'),
    id: element.id,
    name: element.getAttribute('name'),
    autocomplete: element.getAttribute('autocomplete'),
  }));
  const documentUrl = new URL(metadata.documentUrl);
  const editableTypes = new Set(['', 'text', 'email', 'password', 'tel', 'number', 'search', 'url']);
  if (documentUrl.protocol !== 'https:' || documentUrl.username || documentUrl.password
      || metadata.origin !== documentUrl.origin || metadata.readonly
      || !['INPUT', 'TEXTAREA'].includes(metadata.tag)
      || (metadata.tag === 'INPUT' && !editableTypes.has((metadata.type ?? '').toLowerCase()))) {
    throw fail('target_rejected');
  }
  return { url: url.href, origin: documentUrl.origin, tabOrigin: url.origin, metadata: JSON.stringify(metadata) };
}

async function receiveValue(request, { python, config }) {
  const chunks = [];
  let size = 0;
  let metadata = '';
  let child;
  let timer;
  let finished = false;
  try {
    // fd 3 is an inherited private channel, separate from stdout and stderr.
    // Node uses an anonymous pipe on Windows and may use a socketpair on Unix.
    // No named endpoint, clipboard, secret argument, or provider adapter exists.
    child = spawn(python, [worker], {
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'ignore', 'pipe'],
    });
    await new Promise((resolve, reject) => {
      const abort = code => {
        if (finished) return;
        finished = true;
        child.kill();
        reject(fail(code));
      };
      timer = setTimeout(() => abort('handoff_timeout'), timeoutMs);
      child.once('error', () => abort('worker_unavailable'));
      child.stdin.on('error', () => abort('worker_unavailable'));
      child.stdout.on('error', () => abort('handoff_failed'));
      child.stdout.on('data', chunk => {
        if (metadata.length + chunk.length > 4096) return abort('handoff_failed');
        metadata += chunk.toString('utf8');
      });
      child.stdio[3].on('error', () => abort('handoff_failed'));
      child.stdio[3].on('data', chunk => {
        if (finished || size + chunk.length > maximumBytes) {
          chunk.fill(0);
          return abort('handoff_failed');
        }
        size += chunk.length;
        chunks.push(chunk);
      });
      child.once('close', code => {
        if (finished) return;
        finished = true;
        let result;
        try { result = JSON.parse(metadata); } catch { reject(fail('handoff_failed')); return; }
        if (code !== 0 || result.status !== 'ok' || size === 0) {
          // Worker diagnostics are fixed codes, never copied into an exception.
          const permitted = new Set([
            'account_not_found', 'credential_not_declared', 'origin_not_allowed',
            'credentials_not_provisioned', 'profile_invalid', 'profile_not_found',
            'windows_required', 'private_pipe_required', 'localappdata_missing',
            'localappdata_invalid', 'dpapi_unprotect_failed', 'credential_envelope_invalid',
            'custody_metadata_invalid', 'custody_path_invalid', 'custody_binding_mismatch',
            'account_registry_invalid', 'browser_credential_unavailable', 'origin_invalid',
            'account_binding_ambiguous', 'account_unavailable', 'credential_invalid',
            'credential_not_provider_bound', 'federated_credentials_not_local',
            'selection_changed', 'credential_read_failed',
            'totp_not_declared', 'totp_not_configured', 'totp_parameters_invalid',
            'totp_enrollment_invalid', 'totp_clock_invalid', 'totp_custody_busy',
            'totp_custody_invalid', 'totp_already_configured', 'totp_enrollment_pending',
            'totp_reference_conflict',
            'registry_invalid', 'registry_not_found', 'backend_unavailable',
            'vault_locked', 'vault_invalid', 'vault_not_found', 'credential_missing',
            'permission_denied', 'clipboard_unavailable', 'request_invalid',
            'account_invalid', 'account_registry_unavailable', 'credential_selector_invalid',
            'credential_reference_conflict', 'credential_readback_mismatch',
            'custody_permissions_invalid', 'custody_busy', 'selection_invalid',
            'platform_unsupported', 'secret_service_locked', 'secret_service_unavailable',
            'secret_service_dependency_missing', 'credential_write_uncertain',
            'clipboard_adapter_required', 'totp_custody_failed',
          ]);
          reject(fail(permitted.has(result.code) ? result.code : 'custody_failed'));
          return;
        }
        resolve();
      });
      child.stdin.end(JSON.stringify({ ...request, ...(config === undefined ? {} : { config }) }));
    });
    return Buffer.concat(chunks, size);
  } finally {
    clearTimeout(timer);
    finished = true;
    for (const chunk of chunks) chunk.fill(0);
    child?.stdin.destroy();
    child?.stdout.destroy();
    child?.stdio[3]?.destroy();
    if (child && child.exitCode === null) child.kill();
    metadata = '';
  }
}

/** Fill one observed field from an existing canonical account record.
 * The caller owns account selection, observed form meaning and submission.
 * The target is { tab, field }, using supported browser-controller objects.
 * This operation never returns the credential or submits the form.
 */
async function fillCredentialWithRuntime(runtime, account, credential, target) {
  let bytes;
  let value;
  let attemptedFill = false;
  try {
    if (typeof account !== 'string' || !/^(?:account\.)?[a-z][a-z0-9_.-]{0,127}$/.test(account)
        || !['password', 'mailbox', 'username', 'security_answer', 'totp_code'].includes(credential)) {
      throw fail('selection_rejected');
    }
    const before = await inspectTarget(target);
    bytes = await receiveValue({ account, credential, origin: before.origin, tab_origin: before.tabOrigin }, runtime);
    const after = await inspectTarget(target);
    if (before.url !== after.url || before.metadata !== after.metadata) throw fail('target_changed');
    value = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    attemptedFill = true;
    await target.field.fill(value, { timeoutMs: 10000 });
    return { status: 'filled', account, credential, origin: after.origin };
  } catch (error) {
    // Browser errors may contain the attempted input. Never forward them,
    // including message, stack, cause, or a provider response.
    throw error instanceof HandoffError ? error : fail(attemptedFill ? 'browser_fill_uncertain' : 'handoff_failed');
  } finally {
    bytes?.fill(0);
    value = undefined;
  }
}

async function totpOperation(runtime, operation, account, { tab }, extra = {}, expectedUrl) {
  let bytes;
  try {
    if (typeof account !== 'string' || !/^(?:account\.)?[a-z][a-z0-9_.-]{0,127}$/.test(account)) throw fail('selection_rejected');
    const url = new URL(await tab.url());
    if (expectedUrl && url.href !== expectedUrl) throw fail('target_changed');
    if (url.protocol !== 'https:' || url.username || url.password) throw fail('target_rejected');
    bytes = await receiveValue({ operation, account, origin: url.origin, ...extra }, runtime);
    if (await tab.url() !== url.href) throw fail('target_changed');
    const result = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
    if (!result || !['not_configured', 'pending', 'active'].includes(result.state)
        || Object.keys(result).some(key => key !== 'state')) throw fail('handoff_failed');
    return { account, origin: url.origin, ...result };
  } catch (error) {
    throw error instanceof HandoffError ? error : fail('handoff_failed');
  } finally {
    bytes?.fill(0);
  }
}

/** Capture only an observed enrollment key/otpauth URI, never a raw caller value.
 * The canonical account record must already declare its unique TOTP reference.
 * This saves pending custody; it neither enters a new credential nor enrolls it.
 */
async function captureEnrollment(runtime, account, { tab, secretField }, options, replacePending) {
  let value;
  try {
    const admitted = await totpOperation(runtime, 'totp_status', account, { tab });
    const url = new URL(await tab.url());
    if (url.origin !== admitted.origin) throw fail('target_changed');
    const inspectSource = async () => {
      if (await secretField.count() !== 1 || !await secretField.isVisible()) throw fail('target_rejected');
      const metadata = await secretField.evaluate(element => ({
        origin: element.ownerDocument.location.origin,
        documentUrl: element.ownerDocument.location.href,
        tag: element.tagName,
        id: element.id,
        name: element.getAttribute('name'),
      }));
      const documentUrl = new URL(metadata.documentUrl);
      if (metadata.origin !== url.origin || documentUrl.origin !== url.origin
          || documentUrl.protocol !== 'https:' || documentUrl.username || documentUrl.password) throw fail('target_rejected');
      return JSON.stringify(metadata);
    };
    const before = await inspectSource();
    value = await secretField.evaluate(element => ['INPUT', 'TEXTAREA'].includes(element.tagName) ? element.value : element.textContent);
    if (typeof value !== 'string' || value.length > 2048) throw fail('target_rejected');
    if (await tab.url() !== url.href || await inspectSource() !== before) throw fail('target_changed');
    return await totpOperation(runtime, 'capture_totp', account, { tab }, { enrollment: value, options, replace_pending: replacePending }, url.href);
  } catch (error) {
    throw error instanceof HandoffError ? error : fail('handoff_failed');
  } finally {
    value = undefined;
  }
}

/** Configure the trusted local Python executable and nonsecret registry path.
 * No caller-supplied credential or replacement transport is accepted.
 * The default executable is python on Windows, python3 elsewhere.
 * Private human clipboard handoff uses the separate interactive CLI so the
 * owning process can preserve and release its OS selection safely.
 */
export function createCredentialHandoff({ python = platform() === 'win32' ? 'python' : 'python3', config } = {}) {
  if (typeof python !== 'string' || !python || /[\0\r\n]/.test(python)
      || (config !== undefined && (typeof config !== 'string' || !isAbsolute(config) || /[\0\r\n]/.test(config)))) {
    throw fail('configuration_rejected');
  }
  const runtime = Object.freeze({ python, config });
  return Object.freeze({
    fillCredential: (account, credential, target) => fillCredentialWithRuntime(runtime, account, credential, target),
    captureTotpEnrollment: (account, target, options = {}) => captureEnrollment(runtime, account, target, options, false),
    // Only after observing the previous pending provider attempt did not complete.
    replacePendingTotpEnrollment: (account, target, options = {}) => captureEnrollment(runtime, account, target, options, true),
    totpStatus: (account, target) => totpOperation(runtime, 'totp_status', account, target),
    // Call only after observing provider acceptance of this enrollment.
    confirmTotpEnrollment: (account, target) => totpOperation(runtime, 'activate_totp', account, target),
  });
}

export const {
  fillCredential, captureTotpEnrollment, replacePendingTotpEnrollment, totpStatus,
  confirmTotpEnrollment,
} = createCredentialHandoff();
