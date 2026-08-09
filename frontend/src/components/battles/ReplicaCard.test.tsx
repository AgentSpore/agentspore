// @vitest-environment jsdom
import { cleanup, render, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ReplicaCard } from "./BattleVerdict";

// This project registers no global setup file, so React Testing Library's
// auto-cleanup never self-installs and rendered DOM accumulates across tests
// in a file. Measured: a marker rendered in one test is still in the document
// during the next. Both guards below matter — the explicit cleanup, and
// scoping every query to the container this test rendered, so a negative
// assertion cannot pass merely because a neighbour happened to run first.
afterEach(cleanup);

const NAMES = { agentAName: "Alpha", agentBName: "Beta" };

describe("ReplicaCard judge attribution", () => {
  it("names the judge model that produced the vote", () => {
    const { container } = render(
      <ReplicaCard
        index={0}
        judgeRef="mistral/mistral-large-latest"
        vote="a"
        confidence={0.8}
        {...NAMES}
      />,
    );
    const card = within(container);

    expect(card.getByText("mistral/mistral-large-latest")).toBeTruthy();
    // The model name REPLACES the anonymous label rather than sitting beside it:
    // a card showing both reads as two separate judges.
    expect(card.queryByText("Replica 1")).toBeNull();
  });

  it("falls back to the replica number when the judge is not known yet", () => {
    const { container } = render(
      <ReplicaCard index={0} vote={null} confidence={null} pending {...NAMES} />,
    );

    expect(within(container).getByText("Replica 1")).toBeTruthy();
  });

  it("does not pass the recusal token off as a model name", () => {
    // The backend writes 'panel/recused' when no judge could be seated. Shown
    // verbatim in the model slot it reads as a model called panel/recused.
    const { container } = render(
      <ReplicaCard
        index={0}
        judgeRef="panel/recused"
        vote="error"
        confidence={null}
        {...NAMES}
      />,
    );
    const card = within(container);

    expect(card.queryByText("panel/recused")).toBeNull();
    expect(card.getByText("no judge seated")).toBeTruthy();
  });
});
