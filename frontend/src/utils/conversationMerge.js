const ensureObject = (value) => (value && typeof value === 'object' ? value : {});

const mergeMetadata = (current, incoming) => {
  if (!current && !incoming) return null;
  const base = ensureObject(current);
  const patch = ensureObject(incoming);
  const merged = {
    ...base,
    ...patch,
  };
  if (base.stage_progress || patch.stage_progress) {
    merged.stage_progress = {
      ...ensureObject(base.stage_progress),
      ...ensureObject(patch.stage_progress),
    };
  }
  return merged;
};

const mergeLoading = (current, incoming) => {
  if (!current && !incoming) return undefined;
  return {
    ...ensureObject(current),
    ...ensureObject(incoming),
  };
};

export const mergeMessage = (current, incoming) => {
  if (!current && !incoming) return null;
  if (!current) return incoming;
  if (!incoming) return current;

  if (current.role && incoming.role && current.role !== incoming.role) {
    return incoming;
  }

  return {
    ...current,
    ...incoming,
    content: incoming.content ?? current.content,
    stage1: incoming.stage1 ?? current.stage1,
    negative: incoming.negative ?? current.negative,
    stage2: incoming.stage2 ?? current.stage2,
    stage3: incoming.stage3 ?? current.stage3,
    trace: incoming.trace ?? current.trace,
    metadata: mergeMetadata(current.metadata, incoming.metadata),
    loading: mergeLoading(current.loading, incoming.loading),
  };
};

export const mergeConversation = (current, incoming) => {
  if (!current && !incoming) return null;
  if (!current) {
    return {
      ...incoming,
      messages: Array.isArray(incoming?.messages) ? incoming.messages : [],
    };
  }
  if (!incoming) return current;

  const currentMessages = Array.isArray(current.messages) ? current.messages : [];
  const incomingMessages = Array.isArray(incoming.messages) ? incoming.messages : [];
  const mergedMessages = incomingMessages.map((message, index) => (
    mergeMessage(currentMessages[index], message)
  ));

  return {
    ...current,
    ...incoming,
    title: incoming.title || current.title,
    created_at: incoming.created_at || current.created_at,
    messages: mergedMessages,
  };
};

export const ensureLoadingState = (message) => {
  if (!message.loading) {
    message.loading = {
      stage1: false,
      negative: false,
      stage2: false,
      deliberation: false,
      stage3: false,
    };
  }
  return message.loading;
};
