import http from "node:http";

const origin = "http://127.0.0.1:3187";
const server = http.createServer((request, response) => {
  if (!["GET", "HEAD"].includes(request.method) || !request.url?.startsWith("/") || request.url.startsWith("//") || request.headers.authorization || request.headers["proxy-authorization"]) {
    response.writeHead(403).end();
    return;
  }
  const upstream = http.request({ hostname: "127.0.0.1", port: 3186, path: request.url, method: request.method, headers: { ...request.headers, host: "127.0.0.1:3186" } }, incoming => {
    const location = incoming.headers.location;
    if (location) {
      try {
        const target = new URL(location, origin);
        if (target.origin !== origin || target.username || target.password) throw new Error("External redirect");
      } catch {
        incoming.resume();
        response.writeHead(502).end();
        return;
      }
    }
    response.writeHead(incoming.statusCode ?? 502, incoming.headers);
    incoming.pipe(response);
  });
  upstream.on("error", () => response.writeHead(502).end());
  upstream.end();
});
server.listen(3187, "127.0.0.1");
