import { useState, useEffect, useCallback } from 'react';
import { authFetch } from '../contexts/AuthContext';

const API = process.env.REACT_APP_BACKEND_URL;
const VAPID_KEY = process.env.REACT_APP_VAPID_PUBLIC_KEY;

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

export function usePushNotifications() {
  const [permission, setPermission] = useState('default');
  const [subscribed, setSubscribed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [supported, setSupported] = useState(false);

  useEffect(() => {
    const isSupported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
    setSupported(isSupported);
    if (isSupported) {
      setPermission(Notification.permission);
    }
    setLoading(false);
  }, []);

  // Check backend subscription status on mount
  useEffect(() => {
    const check = async () => {
      try {
        const res = await authFetch(`${API}/api/push/status`);
        if (res.ok) {
          const data = await res.json();
          setSubscribed(data.subscribed);
        }
      } catch (e) { console.error('Push status check failed:', e); }
    };
    check();
  }, []); // API and authFetch are stable module-level constants

  const subscribe = useCallback(async () => {
    if (!supported) return false;
    try {
      const perm = await Notification.requestPermission();
      setPermission(perm);
      if (perm !== 'granted') return false;

      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(VAPID_KEY),
      });

      const res = await authFetch(`${API}/api/push/subscribe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ subscription: subscription.toJSON() }),
      });

      if (res.ok) {
        setSubscribed(true);
        return true;
      }
      return false;
    } catch (e) {
      console.error('Push subscribe error:', e);
      return false;
    }
  }, [supported]); // API and authFetch are stable module-level constants

  const unsubscribe = useCallback(async () => {
    try {
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();
      if (subscription) await subscription.unsubscribe();

      await authFetch(`${API}/api/push/unsubscribe`, { method: 'POST' });
      setSubscribed(false);
      return true;
    } catch (e) {
      console.error('Push unsubscribe error:', e);
      return false;
    }
  }, []); // API and authFetch are stable module-level constants

  return { permission, subscribed, loading, supported, subscribe, unsubscribe };
}
