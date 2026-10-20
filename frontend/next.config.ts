import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactCompiler: true,
  output: "standalone",
  // INVARIANT(RP.6): Bound static workers to the OSS builder's two-CPU/four-GiB budget; Next defaults follow host CPU count.
  experimental: { cpus: 2 },
};

export default nextConfig;
