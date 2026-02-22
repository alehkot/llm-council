import { useCallback, useEffect, useRef } from 'react';

/**
 * Hook for managing browser notifications.
 * Only sends notifications when the tab is not visible.
 */
export function useNotifications() {
  const permissionRef = useRef(
    typeof Notification !== 'undefined' ? Notification.permission : 'denied'
  );

  // Update permission ref when it changes
  useEffect(() => {
    if (typeof Notification === 'undefined') return;
    permissionRef.current = Notification.permission;
  }, []);

  const requestPermission = useCallback(async () => {
    if (typeof Notification === 'undefined') {
      console.log('[Notifications] Notification API not available');
      return 'denied';
    }

    console.log('[Notifications] Current permission:', Notification.permission);

    if (Notification.permission === 'granted') {
      permissionRef.current = 'granted';
      return 'granted';
    }

    if (Notification.permission === 'denied') {
      console.log('[Notifications] Permission was previously denied');
      return 'denied';
    }

    try {
      console.log('[Notifications] Requesting permission...');
      const result = await Notification.requestPermission();
      console.log('[Notifications] Permission result:', result);
      permissionRef.current = result;
      return result;
    } catch (err) {
      console.error('Failed to request notification permission:', err);
      return 'denied';
    }
  }, []);

  const notify = useCallback((title, options = {}) => {
    // Only notify if tab is hidden
    if (document.visibilityState === 'visible') {
      console.log('[Notifications] Tab is visible, skipping notification');
      return null;
    }

    if (typeof Notification === 'undefined') {
      console.log('[Notifications] Notification API not available');
      return null;
    }

    if (permissionRef.current !== 'granted') {
      console.log('[Notifications] Permission not granted:', permissionRef.current);
      return null;
    }

    try {
      console.log('[Notifications] Sending notification:', title);
      const notification = new Notification(title, {
        icon: '/favicon.ico',
        badge: '/favicon.ico',
        ...options,
      });

      // Focus the tab when notification is clicked
      notification.onclick = () => {
        window.focus();
        notification.close();
      };

      return notification;
    } catch (err) {
      console.error('Failed to show notification:', err);
      return null;
    }
  }, []);

  const notifyInputNeeded = useCallback(() => {
    return notify('Input Required', {
      body: 'The LLM Council is waiting for your response.',
      tag: 'llm-council-input', // Prevents duplicate notifications
    });
  }, [notify]);

  const notifyComplete = useCallback(() => {
    return notify('Response Ready', {
      body: 'The LLM Council has finished deliberating.',
      tag: 'llm-council-complete',
    });
  }, [notify]);

  return {
    requestPermission,
    notify,
    notifyInputNeeded,
    notifyComplete,
  };
}
