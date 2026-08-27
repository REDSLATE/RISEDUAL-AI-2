// Minimal service worker — NO fetch handler.
// The fetch handler was causing "postMessage: Request object could not be cloned"
// errors due to AbortController signals being non-structured-cloneable.
const CACHE_NAME = 'risedualai-v3';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Push notification handler
self.addEventListener('push', (event) => {
  if (!event.data) return;
  try {
    const data = event.data.json();
    const options = {
      body: data.body || '',
      icon: data.icon || '/logo192.png',
      badge: data.badge || '/logo192.png',
      tag: data.tag || 'risedual',
      data: { url: data.url || '/' },
      vibrate: [100, 50, 100],
      actions: [{ action: 'open', title: 'View' }],
    };
    event.waitUntil(self.registration.showNotification(data.title || 'RISEDUAL AI', options));
  } catch (e) {
    // Silently ignore malformed push data
  }
});

// Notification click handler
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = event.notification.data?.url || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    })
  );
});
