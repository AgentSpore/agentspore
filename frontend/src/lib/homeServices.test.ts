import { describe, expect, it } from "vitest";
import { curatedHomeService, homeServiceUrl, parseHomeProjects, visibleHomeServices, type HomeServiceProject } from "./homeServices";

const project: HomeServiceProject = { id: "00000000-0000-4000-8000-000000000001", title: "PodMemory", status: "deployed", repo_url: "https://github.com/AgentSpore/podmemory", deploy_url: "https://podmemory.agentspore.com", description: "API description" };

describe("homepage service boundary", () => {
  it.each(["javascript:alert(1)", "data:text/html,test", "https://user:pass@example.test", "https:example.test", "https://", "https://example.test/\n", "https://example.test\\bad", "//example.test", null])("rejects unsafe URL %s", value => {
    expect(homeServiceUrl(value)).toBeNull();
  });
  it("allows actual Telegram deployment and public HTTPS application URLs", () => {
    expect(homeServiceUrl("https://t.me/PereklichkaAppBot")).toBe("https://t.me/PereklichkaAppBot");
    expect(homeServiceUrl(project.deploy_url)).toBe("https://podmemory.agentspore.com/");
  });
  it.each([null, {}, [null], [{ ...project, id: "../redirect" }], [{ ...project, id: 1 }], [{ ...project, title: null }], [{ ...project, description: {} }]])("rejects malformed catalog %j", value => {
    expect(() => parseHomeProjects(value)).toThrow("Invalid project catalog");
  });
  it("normalizes optional public fields without inventing values", () => {
    expect(parseHomeProjects([{ id: project.id, title: "Example", status: "historical" }])[0]).toEqual({ id: project.id, title: "Example", status: "historical", description: "", repo_url: null, deploy_url: null });
  });
  it("limits deployed safe catalog entries and keeps API order", () => {
    const valid = Array.from({ length: 8 }, (_, i) => ({ ...project, id: `00000000-0000-4000-8000-${String(i).padStart(12, "0")}` }));
    expect(visibleHomeServices([{ ...project, status: "historical" }, { ...project, status: "archived" }, { ...project, deploy_url: "javascript:bad" }, ...valid])).toEqual(valid.slice(0, 6));
  });
  it("matches curated instructions only to the exact platform repository", () => {
    expect(curatedHomeService(project, "en")?.purpose).toContain("backup");
    for (const repo_url of ["https://github.com/elsewhere/podmemory", "https://github.com/AgentSpore/podmemory-fake", "https://github.com/AgentSpore/podmemory?next=bad", "https://github.com.evil.test/AgentSpore/podmemory"]) {
      expect(curatedHomeService({ ...project, repo_url }, "en")).toBeUndefined();
    }
  });
  it("keeps Pereklichka's guide and honest limits in both languages", () => {
    for (const locale of ["en", "ru"] as const) {
      const known = curatedHomeService({ ...project, repo_url: "https://github.com/AgentSpore/pereklichka" }, locale);
      expect(known?.startUrl).toContain("/docs/user-guide.md");
      expect(known?.limits).toMatch(/Do not rely|Не рассчитывайте/);
    }
  });
});
