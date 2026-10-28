import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Execute the production callback with controlled API/store seams, not rendered UI.
const source = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
function harness({ messageCount = 0, deleteConversation, createConversation } = {}) {
  const state = {
    currentConversationId: 'A',
    conversations: [{ id: 'A', message_count: messageCount }, { id: 'B', message_count: 2 }],
    conversationsById: { A: { id: 'A', messages: [] }, B: { id: 'B', messages: [] } },
    error: null,
  };
  const calls = { deleted: [], created: 0, selected: [], urls: [] };
  const setters = Object.fromEntries(['conversations', 'conversationsById', 'error'].map(key => [
    `set${key[0].toUpperCase()}${key.slice(1)}`,
    next => { state[key] = typeof next === 'function' ? next(state[key]) : next; },
  ]));
  const start = '\tconst handleNewConversation = async () => {';
  const end = '\n\tconst handleDeleteConversation';
  assert.equal(source.split(start).length, 2, 'Unique production creation callback marker');
  const body = start + source.split(start)[1].split(end)[0];
  const selectStart = '\tconst selectConversation = useCallback(';
  const selectEnd = '\n\tconst updateConversationById';
  assert.equal(source.split(selectStart).length, 2, 'Unique production selection marker');
  const selection = selectStart + source.split(selectStart)[1].split(selectEnd)[0];
  const callback = () => vm.runInNewContext(`${selection}\n${body}\nhandleNewConversation;`, {
    ...state, ...setters,
    useAppStore: { getState: () => state },
    api: {
      deleteConversation: async id => { calls.deleted.push(id); return deleteConversation?.(id); },
      createConversation: async () => {
        calls.created += 1;
        return createConversation ? createConversation() : { id: 'C', title: 'New C', created_at: '2026-01-01' };
      },
    },
    useCallback: callback => callback,
    setCurrentConversationId: id => { state.currentConversationId = id; calls.selected.push(id); },
    updateConversationUrl: (id, replace) => calls.urls.push({ id, replace }),
    console: { error: () => {} },
    handleApiError: () => false,
  }, { filename: 'App.jsx' });
  return { state, calls, callback };
}

test('failed replacement creation can be retried without deleting the missing consultation', async () => {
  let deleted = false;
  let createAttempts = 0;
  const h = harness({
    deleteConversation: async () => {
      if (deleted) throw new Error('404 Not found');
      deleted = true;
    },
    createConversation: async () => {
      if (++createAttempts === 1) throw new Error('Temporary create failure');
      return { id: 'C', title: 'New C', created_at: '2026-01-01' };
    },
  });
  await h.callback()();
  assert.equal(h.state.currentConversationId, null);
  assert.deepEqual(clone(h.state.conversations.map(c => c.id)), ['B']);
  assert.equal(h.state.conversationsById.A, undefined);
  assert.equal(h.state.error.details, 'Temporary create failure');
  await h.callback()();
  assert.deepEqual(h.calls.deleted, ['A']);
  assert.equal(h.calls.created, 2);
  assert.equal(h.state.currentConversationId, 'C');
  assert.deepEqual(clone(h.state.conversations.map(c => c.id)), ['C', 'B']);
  assert.equal(h.state.error, null);
});

test('failed empty-consultation deletion preserves state and does not create a replacement', async () => {
  const h = harness({ deleteConversation: async () => { throw new Error('Delete failed'); } });
  const before = clone(h.state);
  await h.callback()();
  assert.equal(h.state.currentConversationId, before.currentConversationId);
  assert.deepEqual(clone(h.state.conversations), before.conversations);
  assert.deepEqual(clone(h.state.conversationsById), before.conversationsById);
  assert.equal(h.calls.created, 0);
  assert.equal(h.state.error.details, 'Delete failed');
});

test('successful deletion followed by failed creation preserves newer navigation', async () => {
  let resolveDelete;
  const pendingDelete = new Promise(resolve => { resolveDelete = resolve; });
  const h = harness({
    deleteConversation: () => pendingDelete,
    createConversation: async () => { throw new Error('Create failed'); },
  });
  const running = h.callback()();
  h.state.currentConversationId = 'B';
  h.state.conversations.push({ id: 'D', message_count: 2 });
  h.state.conversationsById.D = { id: 'D', messages: [] };
  resolveDelete();
  await running;
  assert.equal(h.state.currentConversationId, 'B');
  assert.deepEqual(h.calls.selected, []);
  assert.deepEqual(clone(h.state.conversations.map(c => c.id)), ['B', 'D']);
  assert.deepEqual(Object.keys(h.state.conversationsById).sort(), ['B', 'D']);
});

test('successful replacement removes only the empty consultation and selects the new one', async () => {
  const h = harness();
  await h.callback()();
  assert.deepEqual(h.calls.deleted, ['A']);
  assert.equal(h.calls.created, 1);
  assert.equal(h.state.currentConversationId, 'C');
  assert.deepEqual(clone(h.state.conversations.map(c => c.id)), ['C', 'B']);
  assert.deepEqual(Object.keys(h.state.conversationsById).sort(), ['B', 'C']);
});

test('creating from a populated consultation does not delete it', async () => {
  const h = harness({ messageCount: 2 });
  await h.callback()();
  assert.deepEqual(h.calls.deleted, []);
  assert.equal(h.calls.created, 1);
  assert.equal(h.state.currentConversationId, 'C');
  assert.deepEqual(clone(h.state.conversations.map(c => c.id)), ['C', 'A', 'B']);
  assert.deepEqual(Object.keys(h.state.conversationsById).sort(), ['A', 'B', 'C']);
});
