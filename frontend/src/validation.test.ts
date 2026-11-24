import { describe, expect, it } from "vitest";
import {
  MAX_FILTER_VALUE, validateFilterValue, validateHost, validateImportFiles, validateLabel, validatePassword,
  validatePort, validateSearchText, validateTargetIp, validateText, validateUsername,
} from "./validation";

describe("validateText", () => {
  it("allows empty optional values and asks for required ones", () => {
    expect(validateText("  ", "name", { max: 5 })).toBeNull();
    expect(validateText("  ", "your name", { max: 5, required: true })).toBe("Enter your name.");
  });
  it("limits length after trimming", () => {
    expect(validateText(" abcde ", "name", { max: 5 })).toBeNull();
    expect(validateText("abcdef", "name", { max: 5 })).toMatch(/limited to 5 characters/);
  });
  it.each(["a\nb", "a\tb", "a\u0000b", "a​b", "a‮b"])("rejects control or invisible characters in %j", (v) => {
    expect(validateText(v, "name", { max: 20 })).toMatch(/control or invisible/);
  });
  it("accepts ordinary non-ASCII text", () => {
    expect(validateText("Zoë – Röntgen 3", "name", { max: 20 })).toBeNull();
  });
});

describe("validateTargetIp", () => {
  it.each(["192.168.0.3", "0.0.0.0", "255.255.255.255", " 10.0.0.1 "])("accepts %j", (v) => expect(validateTargetIp(v)).toBeNull());
  it.each(["256.1.1.1", "1.2.3", "1.2.3.4.5", "010.0.0.1", "a.b.c.d", "1.2.3.4/24", "::1"])("rejects %j", (v) =>
    expect(validateTargetIp(v)).toMatch(/valid IPv4/));
  it("asks for a value when empty", () => expect(validateTargetIp(" ")).toMatch(/Enter the IP/));
});

describe("validateHost", () => {
  it.each(["lab-pc", "lab-pc.local", "10.1.2.3", "host.example.com."])("accepts %j", (v) => expect(validateHost(v)).toBeNull());
  it.each(["", "-bad.local", "bad-.local", "bad_name", "a..b", "has space", "300.1.1.1", "x".repeat(254)])("rejects %j", (v) =>
    expect(validateHost(v)).not.toBeNull());
});

describe("validatePort", () => {
  it.each(["1", "22", " 65535 "])("accepts %j", (v) => expect(validatePort(v)).toBeNull());
  it.each(["0", "65536", "-1", "", "80a", "1.5", "123456"])("rejects %j", (v) => expect(validatePort(v)).toMatch(/1 and 65535/));
});

describe("login fields", () => {
  it("requires and bounds the username", () => {
    expect(validateUsername("  ")).toBe("Enter your username.");
    expect(validateUsername("alex")).toBeNull();
    expect(validateUsername("u".repeat(129))).toMatch(/limited to 128/);
  });
  it("only requires and bounds the password, never trims it", () => {
    expect(validatePassword("")).toBe("Enter your password.");
    expect(validatePassword("  spaces ok  ")).toBeNull();
    expect(validatePassword("p".repeat(257))).toMatch(/limited to 256/);
  });
});

describe("filters and labels", () => {
  it("caps filter values at the backend limit", () => {
    expect(validateFilterValue("x".repeat(MAX_FILTER_VALUE), "value")).toBeNull();
    expect(validateFilterValue("x".repeat(MAX_FILTER_VALUE + 1), "search text")).toMatch(/Search text is limited to 200/);
  });
  it("labels are optional and capped at 64", () => {
    expect(validateLabel("")).toBeNull();
    expect(validateLabel("x".repeat(65))).toMatch(/limited to 64/);
  });
});

describe("validateSearchText", () => {
  it.each(["nak\treply", "line one\nline two", "a\r\nb"])("accepts tabs and line breaks pasted from logs: %j", (v) =>
    expect(validateSearchText(v)).toBeNull());
  it("still refuses other control and invisible characters", () => {
    expect(validateSearchText("a\u0000b")).toMatch(/control or invisible/);
    expect(validateSearchText("a\u200bb")).toMatch(/control or invisible/);
  });
  it("caps the length at the backend limit, counting folded whitespace", () => {
    expect(validateSearchText("x".repeat(MAX_FILTER_VALUE))).toBeNull();
    expect(validateSearchText("x".repeat(MAX_FILTER_VALUE) + "\ty")).toMatch(/limited to 200/);
  });
});

describe("validateImportFiles", () => {
  it("accepts supported types in any case", () => {
    expect(validateImportFiles(["a.csv", "B.LOG", "c.v2.txt", "d.zip"])).toEqual([]);
  });
  it("names every rejected file", () => {
    const errs = validateImportFiles(["ok.csv", "run.exe", "noext", ".csv"]);
    expect(errs).toHaveLength(3);
    expect(errs[0]).toMatch(/run\.exe/);
  });
  it("rejects very long names", () => {
    expect(validateImportFiles(["x".repeat(256) + ".csv"])).toHaveLength(1);
  });
});
