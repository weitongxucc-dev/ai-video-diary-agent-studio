import { expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ redirect: vi.fn(), oldRead: vi.fn() }));
vi.mock("next/navigation", () => ({ redirect: mocks.redirect }));
vi.mock("@/lib/api.server", () => ({ getPreferences: mocks.oldRead, getPreferenceCards: mocks.oldRead, getPreferenceEvidence: mocks.oldRead }));
import LearningPage from "../page";

it("routes the old learning page to explicit creator preferences without reading or rendering legacy cards", () => {
  const redirectSignal = new Error("NEXT_REDIRECT");
  mocks.redirect.mockImplementationOnce(() => { throw redirectSignal; });
  expect(() => LearningPage()).toThrow(redirectSignal);
  expect(mocks.redirect).toHaveBeenCalledWith("/profile");
  expect(mocks.oldRead).not.toHaveBeenCalled();
});
