import { describe, expect, it } from "vitest";
import { runErrorText, stepLabel } from "./runStatus";

describe("stepLabel", () => {
  it("gives a plain phrase for a known step", () => {
    expect(stepLabel("deploy_tools")).toBe("Running the diagnostics");
    expect(stepLabel("configure")).toBe("Checking the selected tools");
  });
  it("shows an unknown step as-is and null for none", () => {
    expect(stepLabel("mystery_step")).toBe("mystery_step");
    expect(stepLabel(null)).toBeNull();
  });
});

describe("runErrorText", () => {
  it("names the tool and the step when both are known", () => {
    expect(runErrorText({ step: "deploy_tools", plugin: "netshoot", reason: "cluster unreachable" }))
      .toBe("netshoot failed while running the diagnostics: cluster unreachable");
  });
  it("names only the step when there is no tool", () => {
    expect(runErrorText({ step: "configure", plugin: null, reason: "bad option" }))
      .toBe("Failed while checking the selected tools: bad option");
  });
  it("falls back cleanly when neither step nor tool is known", () => {
    expect(runErrorText({ step: null, plugin: null, reason: "The run was interrupted." }))
      .toBe("The run failed: The run was interrupted.");
  });
});
