import { describe, expect, it } from "vitest";
import { sortDevices, type Device } from "./devicesApi";

const d = (ip_address: string, kind: Device["kind"]): Device =>
  ({ id: ip_address, name: ip_address, kind, ip_address, reachable: true, overridden: false });

describe("sortDevices", () => {
  it("puts the hub first, then viewers, unknowns and the system, by address", () => {
    const sorted = sortDevices([
      d("192.0.2.3", "server"), d("192.0.2.40", "unknown"), d("192.0.2.5", "viewer"),
      d("192.0.2.100", "hub"), d("192.0.2.9", "unknown"), d("192.0.2.12", "viewer"),
    ]);
    expect(sorted.map((x) => x.ip_address)).toEqual([
      "192.0.2.100", "192.0.2.5", "192.0.2.12", "192.0.2.9", "192.0.2.40", "192.0.2.3",
    ]);
  });
});
