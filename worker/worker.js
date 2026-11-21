// Optional CORS/HLS proxy for the IPTV Hub dashboard.
//
// Deploy this as a Cloudflare Worker (free plan is enough), then paste its
// URL into the dashboard setting "پروکسی اختیاری". The dashboard sends every
// playlist and segment through this Worker, which:
//   * adds Access-Control-Allow-Origin: * so hls.js can read the responses;
//   * rewrites every URI inside .m3u8 files to go back through the Worker;
//   * streams video segments through unchanged.
//
// Optional: set TOKEN below to a random string. The dashboard URL then needs
// "?token=..." (for example https://my-worker.workers.dev/?token=secret).
// Keep the URL private if the Worker is open, otherwise other people can use it.

const TOKEN = ""; // e.g. "change-me-9f3a"

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET,HEAD,OPTIONS",
  "access-control-allow-headers": "Range,Content-Type,If-Modified-Since",
  "access-control-expose-headers": "Content-Length,Content-Range,Accept-Ranges",
};

function textResponse(body, status = 200) {
  return new Response(body, {
    status,
    headers: {
      ...CORS,
      "content-type": "text/plain; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

export default {
  async fetch(request) {
    const requestUrl = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return textResponse("method not allowed", 405);
    }
    if (TOKEN && requestUrl.searchParams.get("token") !== TOKEN) {
      return textResponse("forbidden", 403);
    }

    const target = requestUrl.searchParams.get("url");
    if (!target) {
      return textResponse("usage: ?url=<encoded stream url>");
    }

    let upstream;
    try {
      upstream = new URL(target);
    } catch {
      return textResponse("invalid url", 400);
    }
    if (upstream.protocol !== "http:" && upstream.protocol !== "https:") {
      return textResponse("only http(s) urls are allowed", 400);
    }

    const headers = new Headers();
    headers.set(
      "user-agent",
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
        "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    );
    headers.set("accept", "*/*");
    const range = request.headers.get("range");
    if (range) headers.set("range", range);

    let res;
    try {
      res = await fetch(upstream.href, { headers, redirect: "follow" });
    } catch (error) {
      return textResponse("upstream fetch failed: " + String(error), 502);
    }

    const finalUrl = res.url || upstream.href;
    const type = (res.headers.get("content-type") || "").toLowerCase();
    const looksLikePlaylist =
      type.includes("mpegurl") ||
      type.includes("application/vnd.apple") ||
      /\.m3u8($|\?)/i.test(upstream.pathname) ||
      /\.m3u8($|\?)/i.test(finalUrl);

    if (looksLikePlaylist) {
      const body = await res.text();
      if (!body.includes("#EXTM3U")) {
        return new Response(body, {
          status: res.status,
          headers: {
            ...CORS,
            "content-type": type || "application/octet-stream",
            "cache-control": "no-store",
          },
        });
      }
      const rewrite = (uri) => {
        try {
          const absolute = new URL(uri, finalUrl).href;
          const proxied = new URL(requestUrl.href);
          proxied.searchParams.set("url", absolute);
          return proxied.href;
        } catch {
          return uri;
        }
      };
      const lines = body.split(/\r?\n/).map((line) => {
        if (!line) return line;
        if (line.startsWith("#")) {
          return line.replace(/URI="([^"]+)"/g, (_m, uri) => `URI="${rewrite(uri)}"`);
        }
        // Some servers emit tags without the leading '#' (e.g. EXT-X-VERSION:6).
        if (/^[A-Z0-9-]+:/i.test(line)) return line;
        return rewrite(line.trim());
      });
      return new Response(lines.join("\n"), {
        status: res.status,
        headers: {
          ...CORS,
          "content-type": "application/vnd.apple.mpegurl; charset=utf-8",
          "cache-control": "no-store",
        },
      });
    }

    const outHeaders = new Headers(CORS);
    for (const [key, value] of res.headers) {
      if (["content-encoding", "content-length", "transfer-encoding", "connection"].includes(key.toLowerCase())) {
        continue;
      }
      outHeaders.set(key, value);
    }
    return new Response(request.method === "HEAD" ? null : res.body, {
      status: res.status,
      headers: outHeaders,
    });
  },
};
