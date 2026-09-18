/** One URL conversion for cockpit telemetry and live voice, including HTTPS. */
export function websocketUrl(api: string, path: string) {
  return api.replace(/^http(s?):/, (_match, secure) => secure ? "wss:" : "ws:") + path;
}

/** Bound mobile requests so a lost Wi-Fi link cannot leave control pending forever. */
export async function gatewayFetch(url: string, options: RequestInit = {}, timeoutMs = 2500) {
  const controller = new AbortController();
  const abort = () => controller.abort(options.signal?.reason);
  if (options.signal?.aborted) abort();
  options.signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => controller.abort(new Error("Gateway request timed out; reconnect before taking control.")), timeoutMs);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    // Include the small command/heartbeat JSON body in the deadline. Receiving
    // headers alone does not mean a mobile request has completed.
    const body = await response.arrayBuffer();
    return new Response([204, 205, 304].includes(response.status) ? null : body, {
      status: response.status, statusText: response.statusText, headers: response.headers,
    });
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", abort);
  }
}
