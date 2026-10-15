const assert = require("node:assert/strict");
const { once } = require("node:events");
const http = require("node:http");
const { createRequire } = require("node:module");
const test = require("node:test");

test("electron-builder's downloader preserves proxy support", { timeout: 10_000 }, async (t) => {
  let directRequests = 0;
  const proxyRequests = [];
  const target = http.createServer((_request, response) => {
    directRequests += 1;
    response.end("direct");
  });
  const proxy = http.createServer((request, response) => {
    proxyRequests.push(request.url);
    response.end("proxied");
  });
  t.after(async () => {
    for (const server of [proxy, target]) {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    }
  });
  target.listen(0, "127.0.0.1");
  await once(target, "listening");
  proxy.listen(0, "127.0.0.1");
  await once(proxy, "listening");

  // Resolve the downloader actually used by the pinned builder, including its
  // scoped dependency override. All requests stay on disposable loopback servers.
  const builderRequire = createRequire(require.resolve("app-builder-lib"));
  const { initializeProxy } = builderRequire("@electron/get");
  process.env.GLOBAL_AGENT_ENVIRONMENT_VARIABLE_NAMESPACE = "GLOBAL_AGENT_";
  process.env.GLOBAL_AGENT_HTTP_PROXY = `http://127.0.0.1:${proxy.address().port}`;
  // @electron/get falls back to inherited NO_PROXY when this is empty.
  // A host's loopback bypass must not disable the disposable test proxy.
  process.env.GLOBAL_AGENT_NO_PROXY = "cutvoke-proxy-test.invalid";
  initializeProxy();

  const url = `http://127.0.0.1:${target.address().port}/electron-artifact.zip`;
  const body = await new Promise((resolve, reject) => {
    const request = http.get(url, { timeout: 3000 }, (response) => {
      let content = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => { content += chunk; });
      response.once("end", () => resolve(content));
      response.once("error", reject);
    });
    request.once("timeout", () => request.destroy(new Error("local proxy timed out")));
    request.once("error", reject);
  });
  assert.equal(body, "proxied");
  assert.equal(directRequests, 0);
  assert.deepEqual(proxyRequests, [url]);
});
