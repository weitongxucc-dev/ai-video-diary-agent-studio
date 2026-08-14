import { describe, it, expect } from "vitest";
import { isAbsolutePath, validateAbsolutePath } from "../absolutePath";

describe("isAbsolutePath", () => {
  it("accepts a Windows drive-letter absolute path with Chinese and spaces", () => {
    expect(isAbsolutePath("C:\\Users\\名字\\Video Inbox")).toBe(true);
  });

  it("accepts a Windows forward-slash drive path", () => {
    expect(isAbsolutePath("C:/Users/名字/Video Inbox")).toBe(true);
  });

  it("accepts a Windows UNC path", () => {
    expect(isAbsolutePath("\\\\server\\share\\Video Inbox")).toBe(true);
  });

  it("accepts a POSIX absolute path with spaces, parentheses and Chinese", () => {
    expect(isAbsolutePath("/Users/名字/Video (Inbox)")).toBe(true);
  });

  it("accepts the filesystem root", () => {
    expect(isAbsolutePath("/")).toBe(true);
  });

  it("rejects empty and whitespace-only strings", () => {
    expect(isAbsolutePath("")).toBe(false);
    expect(isAbsolutePath("   ")).toBe(false);
  });

  it("rejects relative paths", () => {
    expect(isAbsolutePath("Users/名字")).toBe(false);
    expect(isAbsolutePath("video/inbox")).toBe(false);
    expect(isAbsolutePath("~/Documents")).toBe(false);
    expect(isAbsolutePath("..\\Video Inbox")).toBe(false);
  });

  it("rejects Windows drive-relative paths without a separator", () => {
    expect(isAbsolutePath("C:Users")).toBe(false);
    expect(isAbsolutePath("C:")).toBe(false);
  });
});

describe("validateAbsolutePath", () => {
  it("classifies empty and whitespace-only values as empty", () => {
    expect(validateAbsolutePath("")).toEqual({ kind: "empty" });
    expect(validateAbsolutePath("   ")).toEqual({ kind: "empty" });
  });

  it("classifies POSIX and Windows absolute paths as absolute, trimming surrounding whitespace", () => {
    expect(validateAbsolutePath("/Users/名字/Video Inbox")).toEqual({
      kind: "absolute",
      path: "/Users/名字/Video Inbox",
    });
    expect(validateAbsolutePath("  C:\\Users\\名字\\Video Inbox  ")).toEqual({
      kind: "absolute",
      path: "C:\\Users\\名字\\Video Inbox",
    });
  });

  it("classifies relative paths as relative, preserving the trimmed value", () => {
    expect(validateAbsolutePath("video/inbox")).toEqual({
      kind: "relative",
      path: "video/inbox",
    });
    expect(validateAbsolutePath(" 相对路径 ")).toEqual({
      kind: "relative",
      path: "相对路径",
    });
  });
});
