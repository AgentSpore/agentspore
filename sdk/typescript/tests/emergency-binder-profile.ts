import { AgentSpore } from "../src/client.js";

export async function createEmergencyBinder(client: AgentSpore) {
  return client.createProject({
    title: "Binder",
    description: "Synthetic type consumer",
    metricsProfile: "emergency_binder",
  });
}
