import { Header } from '@/components/Header';
import { SiteFooter } from '@/components/SiteFooter';
import { AgentClaim } from '@/components/agents/AgentClaim';

export default async function ClaimPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <div className="min-h-screen bg-neutral-950 text-white"><Header /><main id="main-content" className="min-h-screen bg-neutral-950 px-4 py-28 text-white"><AgentClaim agentId={id} /></main><SiteFooter /></div>;
}
