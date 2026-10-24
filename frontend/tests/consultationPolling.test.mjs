import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Execute the production effect bodies, not a reimplementation of their logic.
// The harness supplies effect cleanup, controlled promises, and store/API seams.
// It deliberately does not claim to render or exercise browser interaction.
const source = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');
const markers = {
  poll: ['// Poll for consultation status', '// Check for active consultation when switching conversations'],
  selection: ['// Check for active consultation when switching conversations', '// Poll for global consultation status'],
};
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const flush = () => new Promise((resolve) => setImmediate(resolve));
const clone = (value) => JSON.parse(JSON.stringify(value));

function harness(kind, overrides = {}) {
  const state = {
    currentConversationId: 'A',
    consultationStatus: { status: 'stage1', consultation_id: 'A-run' },
    isLoading: true,
    messageQueue: [],
    currentInteractivePrompt: null,
    interactiveSessionId: null,
    error: null,
    conversationsById: { A: { id: 'A', messages: [{ role: 'assistant' }] } },
    ...overrides.state,
  };
  const calls = { status: 0, queue: 0, conversation: 0, errors: [], snapshots: [], cleared: [], lists: 0 };
  let cleanup;
  let tick;
  const setters = Object.fromEntries([
    'consultationStatus', 'isLoading', 'messageQueue', 'currentInteractivePrompt',
    'interactiveSessionId', 'error', 'streamingConsultations',
  ].map((key) => [`set${key[0].toUpperCase()}${key.slice(1)}`, (value) => {
    state[key] = typeof value === 'function' ? value(state[key]) : value;
  }]));
  const api = {
    getConsultationStatus: async () => { calls.status++; return overrides.status ? overrides.status() : { status: 'stage1' }; },
    getConversationQueue: async () => { calls.queue++; return overrides.queue ? overrides.queue() : { queue: [] }; },
    getConversation: async () => { calls.conversation++; return overrides.conversation ? overrides.conversation() : state.conversationsById.A; },
  };
  const context = {
    ...setters,
    currentConversationId: state.currentConversationId,
    consultationStatus: state.consultationStatus,
    useAppStore: { getState: () => state },
    useEffect: (effect) => { cleanup = effect(); },
    setInterval: (callback) => { tick = callback; return 1; },
    clearInterval: () => { tick = null; },
    api,
    console: { error: () => {} },
    isActiveConsultationStatus: (status) => Boolean(status && !['none', 'complete', 'failed'].includes(status.status)),
    applyConsultationStatusSnapshot: (id, status) => calls.snapshots.push([id, status]),
    applyConversationSnapshot: (conversation) => { state.conversationsById[conversation.id] = conversation; },
    clearStreamingConsultation: (id) => calls.cleared.push(id),
    loadConversations: () => { calls.lists++; },
    // Baseline also uses this existing helper; keep its API behavior in the harness.
    loadConversation: async (id) => { state.conversationsById[id] = await api.getConversation(id); },
    handleApiError: (error) => calls.errors.push(error.message),
    updateConversationById: (id, update) => update(state.conversationsById[id]),
    findAssistantMessageIndexForStatus: (messages, status) => status.assistant_index ?? (messages.length - 1),
    ensureLoadingState: (message) => (message.loading ||= {}),
  };
  const [start, end] = markers[kind];
  assert.equal(source.split(start).length, 2, `Unique ${kind} start marker`);
  const body = source.split(start)[1].split(end)[0];
  assert.ok(body.includes('useEffect('), `Missing production ${kind} effect`);
  vm.runInNewContext(body, context, { filename: 'App.jsx' });
  const stop = () => cleanup?.();
  const selectB = ({ clean = true } = {}) => {
    if (clean) stop();
    Object.assign(state, {
      currentConversationId: 'B', consultationStatus: { status: 'stage3', consultation_id: 'B-run' },
      isLoading: true, messageQueue: [{ id: 'B-queued' }],
      currentInteractivePrompt: { session_id: 'B-session' }, interactiveSessionId: 'B-session',
      error: null,
    });
  };
  return { state, calls, stop, selectB, tick: () => tick?.() };
}

for (const kind of ['selection', 'poll']) {
  test(`${kind}: late status cannot replace another conversation's state`, async () => {
    const pending = deferred();
    const h = harness(kind, { status: () => pending.promise });
    const running = kind === 'poll' ? h.tick() : undefined;
    h.selectB();
    const expected = clone(h.state);
    pending.resolve({ status: 'none' });
    await running;
    await flush();
    assert.deepEqual(clone(h.state), expected);
    assert.equal(h.calls.queue, 0);
  });

  test(`${kind}: live ID guard works before React runs effect cleanup`, async () => {
    const pending = deferred();
    const h = harness(kind, { status: () => pending.promise });
    const running = kind === 'poll' ? h.tick() : undefined;
    h.selectB({ clean: false });
    const expected = clone(h.state);
    pending.resolve({ status: 'failed', error: 'A failed' });
    await running;
    await flush();
    assert.deepEqual(clone(h.state), expected);
    h.stop();
  });

  test(`${kind}: late queue cannot overwrite another conversation's queue`, async () => {
    const pending = deferred();
    const h = harness(kind, { queue: () => pending.promise });
    const running = kind === 'poll' ? h.tick() : undefined;
    await flush();
    assert.equal(h.calls.queue, 1);
    h.selectB();
    const expected = clone(h.state);
    pending.resolve({ queue: [{ id: 'A-queued' }] });
    await running;
    await flush();
    assert.deepEqual(clone(h.state), expected);
  });

  test(`${kind}: disposed A effect stays obsolete after A→B→A navigation`, async () => {
    const pending = deferred();
    const h = harness(kind, { status: () => pending.promise });
    const running = kind === 'poll' ? h.tick() : undefined;
    h.selectB();
    h.state.currentConversationId = 'A';
    h.state.consultationStatus = { status: 'stage2', consultation_id: 'fresh-A' };
    const expected = clone(h.state);
    pending.resolve({ status: 'none' });
    await running;
    await flush();
    assert.deepEqual(clone(h.state), expected);
  });

  for (const stage of ['status', 'queue']) {
    test(`${kind}: obsolete ${stage} failures skip the effect-local error handler`, async () => {
      const pending = deferred();
      const h = harness(kind, { [stage]: () => pending.promise });
      const running = kind === 'poll' ? h.tick() : undefined;
      await flush();
      h.selectB();
      pending.reject(new Error('obsolete request failure'));
      await running;
      await flush();
      assert.deepEqual(h.calls.errors, []);
    });
  }

  test(`${kind}: current request failures still reach error handling`, async () => {
    const h = harness(kind, { status: async () => { throw new Error('current failure'); } });
    if (kind === 'poll') await h.tick();
    await flush();
    assert.deepEqual(h.calls.errors, ['current failure']);
    h.stop();
  });

  test(`${kind}: successful current status and queue still apply`, async () => {
    const h = harness(kind, {
      status: async () => ({ status: 'stage2', consultation_id: 'current-run' }),
      queue: async () => ({ queue: [{ id: 'current-queued' }] }),
    });
    if (kind === 'poll') await h.tick();
    await flush();
    assert.equal(h.state.consultationStatus.status, 'stage2');
    assert.equal(h.state.messageQueue[0].id, 'current-queued');
    assert.equal(h.state.isLoading, true);
    assert.equal(h.calls.snapshots.length, 1);
    h.stop();
  });
}

for (const outcome of ['success', 'failure']) {
  test(`poll: obsolete conversation snapshot ${outcome} is ignored`, async () => {
    const pending = deferred();
    const h = harness('poll', {
      state: { conversationsById: {} },
      status: async () => ({ status: 'interactive', assistant_index: 0, metadata: { interactive_prompt: { session_id: 'A-session' } } }),
      conversation: () => pending.promise,
    });
    const running = h.tick();
    await flush();
    assert.equal(h.calls.conversation, 1);
    h.selectB();
    const expected = clone(h.state);
    if (outcome === 'success') pending.resolve({ id: 'A', messages: [{ role: 'assistant' }] });
    else pending.reject(new Error('obsolete snapshot failure'));
    await running;
    assert.deepEqual(clone(h.state), expected);
    assert.deepEqual(h.calls.errors, []);
  });
}

test('poll: a slow request does not overlap, and subsequent ticks still work', async () => {
  const pending = deferred();
  const h = harness('poll', { status: () => pending.promise });
  const first = h.tick();
  const overlapping = h.tick();
  await flush();
  const callsWhilePending = h.calls.status;
  pending.resolve({ status: 'stage1' });
  await Promise.all([first, overlapping]);
  assert.equal(callsWhilePending, 1);
  await h.tick();
  assert.equal(h.calls.status, 2);
  h.stop();
});

test('poll: failed request releases the in-flight guard for later ticks', async () => {
  const h = harness('poll', { status: async () => { throw new Error('temporary'); } });
  await h.tick();
  await h.tick();
  assert.equal(h.calls.status, 2);
  h.stop();
});

test('poll: current interactive prompt and recovered snapshot still apply', async () => {
  const h = harness('poll', {
    state: { conversationsById: {} },
    status: async () => ({ status: 'interactive', assistant_index: 0, metadata: { interactive_prompt: { session_id: 'A-session' } } }),
    conversation: async () => ({ id: 'A', messages: [{ role: 'assistant' }] }),
  });
  await h.tick();
  assert.equal(h.state.interactiveSessionId, 'A-session');
  assert.equal(h.state.currentInteractivePrompt.session_id, 'A-session');
  assert.equal(h.calls.conversation, 1);
  assert.equal(h.state.conversationsById.A.messages[0].metadata.interactive_prompt.session_id, 'A-session');
  h.stop();
});

for (const status of ['complete', 'failed', 'none']) {
  test(`poll: current ${status} result retains terminal behavior`, async () => {
    const h = harness('poll', { status: async () => ({ status, error: status === 'failed' ? 'Cancelled' : undefined }) });
    await h.tick();
    assert.equal(h.state.consultationStatus, null);
    assert.equal(h.state.isLoading, false);
    assert.equal(h.state.error, null);
    assert.deepEqual(h.calls.cleared, ['A']);
    h.stop();
  });
}
