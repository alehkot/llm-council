import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');
const start = '\tconst handleInteractiveSubmit = async (responses) => {';
const end = '\n\tconst handleVersionAdded';
const body = start + source.split(start)[1].split(end)[0];
assert.equal(source.split(start).length, 2);
function harness(action) {
  let resolve, reject;
  const pending = new Promise((yes, no) => { resolve = yes; reject = no; });
  const state = { currentConversationId: 'A', interactiveSessionId: 'session-A', currentInteractivePrompt: { prompt_id: 'prompt-A', title: 'Round 1' }, error: null };
  const calls = [];
  let authCalls = 0;
  const callback = vm.runInNewContext(`${body}\n${action === 'submit' ? 'handleInteractiveSubmit' : 'handleInteractiveSkip'};`, {
    ...state,
    api: { submitInteractiveResponse: (...args) => { calls.push(args); return pending; } },
    useAppStore: { getState: () => state },
    setCurrentInteractivePrompt: value => { state.currentInteractivePrompt = value; },
    setInteractiveSessionId: value => { state.interactiveSessionId = value; },
    setError: value => { state.error = value; },
    handleApiError: error => { authCalls++; return error.auth === true; },
    console: { error: () => {} },
  });
  return { state, calls, callback, resolve, reject, authCalls: () => authCalls };
}
for (const action of ['submit', 'skip']) {
  test(`${action}: clears the same logical prompt after a polling refresh`, async () => {
    const h = harness(action); const p = h.callback({ answer: 'test' });
    h.state.currentInteractivePrompt = { prompt_id: 'prompt-A', title: 'Round 1' };
    h.resolve(); await p;
    assert.equal(h.state.currentInteractivePrompt, null);
    assert.equal(h.state.interactiveSessionId, null);
    assert.deepEqual(JSON.parse(JSON.stringify(h.calls)), [['session-A', 'prompt-A', action === 'submit' ? { answer: 'test' } : {}, action === 'skip']]);
  });
  for (const changed of ['conversation', 'session', 'prompt', 'round']) {
    test(`${action}: preserves newer ${changed} on late success`, async () => {
      const h = harness(action); const p = h.callback({ answer: 'test' });
      if (changed === 'conversation') h.state.currentConversationId = 'B';
      if (changed === 'session') h.state.interactiveSessionId = 'session-B';
      if (changed === 'round') h.state.currentInteractivePrompt = { prompt_id: 'prompt-A', title: 'Round 2' };
      if (changed === 'prompt') h.state.currentInteractivePrompt = { prompt_id: 'prompt-B' };
      const expected = JSON.stringify(h.state);
      h.resolve(); await p;
      assert.equal(JSON.stringify(h.state), expected);
    });
  }
  test(`${action}: stale non-auth failure does not replace current conversation error`, async () => {
    const h = harness(action); const p = h.callback({});
    h.state.currentConversationId = 'B'; h.state.error = { message: 'Current B error' };
    h.reject(new Error('Old A failure')); await p;
    assert.equal(h.state.error.message, 'Current B error');
    assert.equal(h.authCalls(), 1);
  });
  test(`${action}: current failure remains retryable and visible`, async () => {
    const h = harness(action); const p = h.callback({});
    h.reject(new Error('Current failure')); await p;
    assert.equal(h.state.currentInteractivePrompt.prompt_id, 'prompt-A');
    assert.equal(h.state.interactiveSessionId, 'session-A');
    assert.equal(h.state.error.details, 'Current failure');
  });
  test(`${action}: authentication failure still reaches global handler after navigation`, async () => {
    const h = harness(action); const p = h.callback({}); h.state.currentConversationId = 'B';
    h.reject(Object.assign(new Error('Unauthorized'), { auth: true })); await p;
    assert.equal(h.authCalls(), 1); assert.equal(h.state.error, null);
  });
}
