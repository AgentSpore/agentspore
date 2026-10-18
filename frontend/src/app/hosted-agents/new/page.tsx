import { redirect } from "next/navigation";

/** Legacy creation URL now opens external-agent connection instructions. */
export default function NewHostedAgentPage() {
  redirect("/workspace#connect-agent");
}
