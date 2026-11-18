self.addEventListener("install", (event) => {
  self.skipWaiting();
});
self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});
self.addEventListener("fetch", (event) => {
  // Network-first pass-through so telemetry, stacks & media stay real-time
  if (event.request.method !== "GET") return;
});
