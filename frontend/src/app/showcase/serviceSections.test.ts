import { describe, expect, it } from "vitest";
import type { Project } from "@/lib/api";
import { CURATED_SERVICES } from "./curatedServices";
import { repoSlug } from "./descriptionText";
import { splitServices } from "./serviceSections";

const project = (slug: string, over: Record<string, unknown> = {}) =>
  ({ id: slug, title: slug, status: "building", repo_url: `https://github.com/AgentSpore/${slug}`, ...over }) as unknown as Project;
const slugs = (items: readonly Project[]) => items.map(item => repoSlug(item.repo_url));
const curated = CURATED_SERVICES.en;

describe("splitServices hides Nuvistela from the showcase", () => {
  it("keeps a non-live Nuvistela out of the archive while another project is archived", () => {
    const { archive } = splitServices([project("nuvistela", { service_live: false }), project("oldtool", { service_live: false })], curated);
    expect(slugs(archive)).toContain("oldtool");
    expect(slugs(archive)).not.toContain("nuvistela");
  });

  it("keeps a live Nuvistela out of every section", () => {
    const sections = splitServices([project("nuvistela", { service_live: true }), project("freshtool", { service_live: true })], curated);
    expect(slugs(sections.liveOther)).toContain("freshtool");
    expect(slugs(sections.liveOther)).not.toContain("nuvistela");
    expect(sections.liveCurated.map(service => service.id)).not.toContain("nuvistela");
    expect(slugs(sections.archive)).not.toContain("nuvistela");
  });

  it("keeps Nuvistela out of the archive during the rollout fallback", () => {
    const { archive } = splitServices([project("nuvistela"), project("oldtool")], curated);
    expect(slugs(archive)).toContain("oldtool");
    expect(slugs(archive)).not.toContain("nuvistela");
  });
});
