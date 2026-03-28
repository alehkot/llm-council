/**
 * API client for the LLM Council backend.
 */

import { apiKeyStorage } from './utils/apiKeyStorage';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8001';
const API_KEY_HEADER = 'X-API-Key';

/**
 * Check if we're in a secure context (HTTPS or localhost).
 * API requests should only be made in secure contexts to protect the API key.
 */
const isSecureContext = () => {
  const hostname = window.location.hostname;
  const isLocalhost = ['localhost', '127.0.0.1', '::1'].includes(hostname);
  const isHttps = window.location.protocol === 'https:';
  return isLocalhost || isHttps;
};

/**
 * Make an API request with the stored API key.
 * Throws typed errors for the UI to handle:
 * - 'NO_API_KEY' - No API key in storage
 * - 'INVALID_API_KEY' - Backend rejected the key (401/403)
 * - 'API_KEY_RATE_LIMITED' - Too many invalid attempts (429)
 * - 'INSECURE_CONTEXT' - Not HTTPS (except localhost)
 */
const apiFetch = async (path, options = {}) => {
  if (!isSecureContext()) {
    throw new Error('INSECURE_CONTEXT');
  }

  const apiKey = apiKeyStorage.get();
  if (!apiKey) {
    throw new Error('NO_API_KEY');
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      ...options.headers,
      [API_KEY_HEADER]: apiKey,
    },
  });

  if (response.status === 429) {
    const retryAfter = response.headers.get('Retry-After');
    const retryAfterSeconds = retryAfter ? Number(retryAfter) : null;
    const error = new Error('API_KEY_RATE_LIMITED');
    if (Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0) {
      error.retryAfterSeconds = retryAfterSeconds;
    }
    throw error;
  }

  // Auto-clear on auth failure and throw typed error
  if (response.status === 401 || response.status === 403) {
    apiKeyStorage.clear();
    throw new Error('INVALID_API_KEY');
  }

  return response;
};

/**
 * Parse filename from Content-Disposition header.
 */
const parseFilenameFromContentDisposition = (headerValue) => {
  if (!headerValue) return null;

  const utf8Match = headerValue.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match && utf8Match[1]) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return utf8Match[1];
    }
  }

  const simpleMatch = headerValue.match(/filename="?([^";]+)"?/i);
  return simpleMatch && simpleMatch[1] ? simpleMatch[1] : null;
};

export const api = {
  /**
   * List all conversations.
   */
  async listConversations() {
    const response = await apiFetch('/api/conversations');
    if (!response.ok) {
      throw new Error('Failed to list conversations');
    }
    return response.json();
  },

  /**
   * Create a new conversation.
   */
  async createConversation() {
    const response = await apiFetch('/api/conversations', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({}),
    });
    if (!response.ok) {
      throw new Error('Failed to create conversation');
    }
    return response.json();
  },

  /**
   * Get a specific conversation.
   */
  async getConversation(conversationId) {
    const response = await apiFetch(`/api/conversations/${conversationId}`);
    if (!response.ok) {
      throw new Error('Failed to get conversation');
    }
    return response.json();
  },

  /**
   * Delete a conversation.
   */
  async deleteConversation(conversationId) {
    const response = await apiFetch(`/api/conversations/${conversationId}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      throw new Error('Failed to delete conversation');
    }
    return response.json();
  },

  /**
   * Delete all conversations.
   */
  async clearConversations() {
    const response = await apiFetch('/api/conversations', {
      method: 'DELETE',
    });
    if (!response.ok) {
      throw new Error('Failed to delete conversations');
    }
    return response.json();
  },

  /**
   * Get backend config flags.
   */
  async getConfig() {
    const response = await apiFetch('/api/config');
    if (!response.ok) {
      throw new Error('Failed to get config');
    }
    return response.json();
  },

  /**
   * Send a message in a conversation.
   * Returns immediately with consultation_id and status (started/queued).
   * Use getConsultationStatus to poll for results.
   */
  async sendMessage(conversationId, content, negativePrompt) {
    const response = await apiFetch(`/api/conversations/${conversationId}/message`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        content,
        negative_prompt: negativePrompt || undefined,
      }),
    });
    if (!response.ok) {
      throw new Error('Failed to send message');
    }
    return response.json();
  },

  /**
   * Open, resume, or create a side-chat thread for a specific council artifact.
   */
  async openSideChat(conversationId, payload) {
    const response = await apiFetch(`/api/conversations/${conversationId}/side-chats/open`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Failed to open side chat' }));
      throw new Error(error.detail || 'Failed to open side chat');
    }
    return response.json();
  },

  /**
   * Send a follow-up message in a persisted side-chat thread.
   */
  async sendSideChatMessage(conversationId, threadId, messageIndex, content) {
    const response = await apiFetch(
      `/api/conversations/${conversationId}/side-chats/${threadId}/message`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message_index: messageIndex,
          content,
        }),
      }
    );
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Failed to send side-chat message' }));
      throw new Error(error.detail || 'Failed to send side-chat message');
    }
    return response.json();
  },


  /**
   * Submit a response to an interactive prompt.
   * @param {string} sessionId - The interactive session ID
   * @param {string} promptId - The prompt ID being responded to
   * @param {object} responses - The user's responses
   * @param {boolean} skipped - Whether the user skipped the prompt
   */
  async submitInteractiveResponse(sessionId, promptId, responses, skipped = false) {
    const response = await apiFetch('/api/interactive-response', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        session_id: sessionId,
        prompt_id: promptId,
        responses,
        skipped,
      }),
    });

    if (!response.ok) {
      throw new Error('Failed to submit interactive response');
    }

    return response.json();
  },

  /**
   * Re-run synthesis with different settings.
   * Appends a new version and sets it as current.
   * @param {string} conversationId - The conversation ID
   * @param {number} messageIndex - The assistant message index (0-based)
   * @param {string} synthesisMode - The synthesis mode (consensus, best_of_breed, comprehensive, weighted)
   * @param {string} [focusAreas] - Optional focus areas for synthesis
   * @returns {Promise<{status: string, version: object, version_index: number}>}
   */
  async resynthesize(conversationId, messageIndex, synthesisMode, focusAreas = null) {
    const response = await apiFetch(`/api/conversations/${conversationId}/resynthesize`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        message_index: messageIndex,
        synthesis_mode: synthesisMode,
        focus_areas: focusAreas || undefined,
      }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Resynthesis failed' }));
      throw new Error(error.detail || 'Resynthesis failed');
    }

    return response.json();
  },

  /**
   * Export all context for one assistant response as markdown.
   * @param {string} conversationId - The conversation ID
   * @param {number} messageIndex - The assistant message index (0-based)
   * @returns {Promise<{blob: Blob, filename: string}>}
   */
  async exportMarkdown(conversationId, messageIndex) {
    const params = new URLSearchParams({ message_index: String(messageIndex) });
    const response = await apiFetch(
      `/api/conversations/${conversationId}/export-markdown?${params.toString()}`
    );

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Export failed' }));
      throw new Error(error.detail || 'Export failed');
    }

    const filename = parseFilenameFromContentDisposition(
      response.headers.get('Content-Disposition')
    ) || 'llm-council-context.md';

    return {
      blob: await response.blob(),
      filename,
    };
  },

  /**
   * Run synthesis for multiple modes in parallel.
   * @param {string} conversationId - The conversation ID
   * @param {number} messageIndex - The assistant message index (0-based)
   * @param {string[]} [modes] - Optional specific modes to generate. If not provided, generates all missing modes.
   * @returns {Promise<{status: string, versions: object[], version_indices: number[], errors?: object[]}>}
   */
  async resynthesizeAll(conversationId, messageIndex, modes = null) {
    const response = await apiFetch(`/api/conversations/${conversationId}/resynthesize-all`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        message_index: messageIndex,
        modes: modes || undefined,
      }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Resynthesis failed' }));
      throw new Error(error.detail || 'Resynthesis failed');
    }

    return response.json();
  },

  /**
   * Set a specific synthesis version as the current one.
   * @param {string} conversationId - The conversation ID
   * @param {number} messageIndex - The assistant message index (0-based)
   * @param {number} versionIndex - The version index to set as current
   * @returns {Promise<{status: string}>}
   */
  async setCurrentVersion(conversationId, messageIndex, versionIndex) {
    const response = await apiFetch(`/api/conversations/${conversationId}/set-current-version`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        message_index: messageIndex,
        version_index: versionIndex,
      }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Failed to set current version' }));
      throw new Error(error.detail || 'Failed to set current version');
    }

    return response.json();
  },

  /**
   * Get consultation status for a conversation.
   */
  async getConsultationStatus(conversationId) {
    const response = await apiFetch(`/api/conversations/${conversationId}/consultation/status`);
    if (!response.ok) {
      throw new Error('Failed to get consultation status');
    }
    return response.json();
  },

  /**
   * Get message queue for a conversation.
   */
  async getConversationQueue(conversationId) {
    const response = await apiFetch(`/api/conversations/${conversationId}/queue`);
    if (!response.ok) {
      throw new Error('Failed to get conversation queue');
    }
    return response.json();
  },

  /**
   * Get global consultation system status.
   */
  async getGlobalConsultationStatus() {
    const response = await apiFetch('/api/consultations/status');
    if (!response.ok) {
      throw new Error('Failed to get consultation status');
    }
    return response.json();
  },

  /**
   * Cancel an in-progress consultation.
   */
  async cancelConsultation(conversationId) {
    const response = await apiFetch(`/api/conversations/${conversationId}/consultation/cancel`, {
      method: 'POST',
    });
    if (!response.ok) {
      throw new Error('Failed to cancel consultation');
    }
    return response.json();
  },
};
