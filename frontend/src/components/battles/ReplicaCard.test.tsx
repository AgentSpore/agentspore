// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReplicaCard } from "./BattleVerdict";

const NAMES = { agentAName: "Alpha", agentBName: "Beta" };

describe("ReplicaCard judge attribution", () => {
  it("names the judge model that produced the vote", () => {
    render(
      <ReplicaCard
        index={0}
        judgeRef="mistral/mistral-large-latest"
        vote="a"
        confidence={0.8}
        {...NAMES}
      />,
    );

    expect(screen.getByText("mistral/mistral-large-latest")).toBeTruthy();
    // The model name REPLACES the anonymous label rather than sitting beside it:
    // a card showing both reads as two separate judges.
    expect(screen.queryByText("Replica 1")).toBeNull();
  });

  it("falls back to the replica number when the judge is not known yet", () => {
    render(<ReplicaCard index={2} vote={null} confidence={null} pending {...NAMES} />);

    expect(screen.getByText("Replica 3")).toBeTruthy();
  });
});
