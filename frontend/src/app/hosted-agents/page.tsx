import { redirect } from "next/navigation";

/** Legacy roster URL now opens the external-agent workspace. */
export default function HostedAgentsPage() {
  redirect("/workspace");
}
