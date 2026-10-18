import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const { AgentSpore } = await import(pathToFileURL(process.argv[2]).href);
let observed;
globalThis.fetch = async (url, options) => {
  assert.equal(url, "https://example.invalid/api/v1/agents/projects");
  assert.equal(options.method, "POST");
  observed = JSON.parse(options.body);
  return new Response(JSON.stringify({ id: "synthetic-project", ...observed }));
};
const client = new AgentSpore({ apiKey: "synthetic", baseUrl: "https://example.invalid" });
const project = await client.createProject({
  title: "Binder", description: "Synthetic", metricsProfile: "emergency_binder",
});
assert.deepEqual(observed, {
  title: "Binder", description: "Synthetic", category: "other", tech_stack: [],
  vcs_provider: "github", metrics_profile: "emergency_binder",
});
assert.equal(project.metrics_profile, "emergency_binder");
