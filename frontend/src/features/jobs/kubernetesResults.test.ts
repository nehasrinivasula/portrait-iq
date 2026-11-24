import { describe, expect, it } from "vitest";
import { kubeDetails, nodeState, podProblem, shownOf, type KubeNode, type KubeProblemPod } from "./kubernetesResults";

const node: KubeNode = {
  name: "edge-1", ready: true, ready_reason: "KubeletReady", pressure: [], roles: ["control-plane"],
  kubelet_version: "v1.34.2", cpu_capacity: "16", memory_capacity: "64Gi",
};
const pod: KubeProblemPod = {
  namespace: "pm", name: "axonerouter-1", phase: "Running", ready: "1/1", restarts: 3,
  last_reason: "OOMKilled", last_exit_code: 137, last_finished: null, node: "edge-1",
};

describe("kubernetes results", () => {
  it("reads details only when the result has them", () => {
    expect(kubeDetails({ tool: "kubernetes", error: "x" })).toBeNull();
    expect(kubeDetails({ details: {} })).toBeNull();
    const d = kubeDetails({ details: { namespaces: [], problem_pods: [pod] } })!;
    expect(d.problem_pods_total).toBe(1);
    expect(d.nodes).toEqual([]);
    expect(d.notes).toEqual([]);
  });

  it("describes node state with pressure", () => {
    expect(nodeState(node)).toBe("Ready");
    expect(nodeState({ ...node, ready: false, ready_reason: "KubeletNotReady", pressure: ["DiskPressure"] }))
      .toBe("Not ready (KubeletNotReady), DiskPressure");
  });

  it("explains why a pod is listed", () => {
    expect(podProblem(pod)).toBe("Restarted 3 times, last: OOMKilled (exit 137)");
    expect(podProblem({ ...pod, restarts: 1, last_reason: null })).toBe("Restarted 1 time");
    expect(podProblem({ ...pod, restarts: 0, ready: "0/1", phase: "Pending" })).toBe("Not ready (0/1 containers ready, Pending)");
  });

  it("says when rows were left out", () => {
    expect(shownOf(50, 50)).toBeNull();
    expect(shownOf(50, 120)).toBe("Showing 50 of 120.");
  });
});
