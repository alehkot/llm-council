/**
 * Utility for persisting the per-conversation agent skill selection (the
 * pre-send draft) in localStorage.
 *
 * The applied record for a sent message lives server-side in
 * message.metadata.skills; this only remembers what the composer had picked so
 * the selection survives a reload or a conversation switch.
 */

const STORAGE_PREFIX = 'llm_council_skills_';

const storageKey = (conversationId) => `${STORAGE_PREFIX}${conversationId}`;

const isUsable = (conversationId) => (
  typeof window !== 'undefined'
  && !!window.localStorage
  && conversationId !== null
  && conversationId !== undefined
  && conversationId !== ''
);

export const skillSelectionStorage = {
  /**
   * Get the stored skill ids for a conversation.
   * Never throws: malformed JSON or unavailable storage yields an empty list.
   * @param {string} conversationId - The conversation ID
   * @returns {string[]} The stored skill ids, or [] if none/invalid
   */
  get(conversationId) {
    if (!isUsable(conversationId)) return [];
    try {
      const raw = window.localStorage.getItem(storageKey(conversationId));
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed.filter((id) => typeof id === 'string' && id);
    } catch {
      return [];
    }
  },

  /**
   * Store the skill ids for a conversation. An empty list clears the entry.
   * @param {string} conversationId - The conversation ID
   * @param {string[]} ids - The skill ids to remember
   */
  set(conversationId, ids) {
    if (!isUsable(conversationId)) return;
    const safeIds = Array.isArray(ids)
      ? ids.filter((id) => typeof id === 'string' && id)
      : [];
    try {
      if (!safeIds.length) {
        window.localStorage.removeItem(storageKey(conversationId));
        return;
      }
      window.localStorage.setItem(
        storageKey(conversationId),
        JSON.stringify(safeIds),
      );
    } catch {
      // Storage unavailable or over quota - the draft is best-effort.
    }
  },

  /**
   * Clear the stored skill ids for a conversation.
   * @param {string} conversationId - The conversation ID
   */
  clear(conversationId) {
    if (!isUsable(conversationId)) return;
    try {
      window.localStorage.removeItem(storageKey(conversationId));
    } catch {
      // Nothing to do - storage is unavailable.
    }
  },
};
