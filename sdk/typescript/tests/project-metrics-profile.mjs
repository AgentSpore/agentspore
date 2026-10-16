import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const { AgentSpore } = await import(pathToFileURL(process.argv[2]).href);
for (const profile of [undefined, "family_safety"]) {
  let observed;
  globalThis.fetch = async (url, options) => {
    assert.equal(url, "https://example.invalid/api/v1/agents/projects");
    assert.equal(options.method, "POST");
    assert.equal(options.headers["X-API-Key"], "synthetic");
    observed = JSON.parse(options.body);
    return new Response(JSON.stringify({ id: "synthetic-project", ...observed }));
  };
  const client = new AgentSpore({ apiKey: "synthetic", baseUrl: "https://example.invalid" });
  const params = { title: "Family safety", description: "Synthetic" };
  if (profile !== undefined) params.metricsProfile = profile;
  const result = await client.createProject(params);
  const expected = {
    title: params.title, description: params.description, category: "other",
    tech_stack: [], vcs_provider: "github",
  };
  if (profile !== undefined) expected.metrics_profile = profile;
  assert.deepEqual(observed, expected);
  assert.equal(result.metrics_profile, profile);
}
