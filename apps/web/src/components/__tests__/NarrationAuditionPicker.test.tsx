import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { NarrationAuditionPicker } from "../creation-task/NarrationAuditionPicker";
import { saveSectionAudition, type SectionAuditionInput } from "@/lib/api/section-audition";
import type { CreationTaskScript } from "@/lib/types";

const fixture = vi.hoisted(() => ({
  revision: "2026-11-19T09:10:00Z",
  candidate: { preview_run_id: "take-2", output_url: "/takes/take-2.wav", duration_seconds: 6.4, output_sha256: "c".repeat(64) },
  chosen: false,
}));

vi.mock("@/lib/api/creation-tasks", () => ({
  getCreationTaskScriptSections: async () => ({
    total_count: 1, next_after_ordinal: null,
    items: [{ ordinal: 1, section: { id: "opening", title: "开场", content: "Yesterday." } }],
  }),
}));
vi.mock("@/lib/api/section-audition", () => {
  const read = async () => ({
    available: true, reason: null, candidates: [fixture.candidate],
    selection: fixture.chosen ? fixture.candidate : null,
  });
  return {
    readSectionAudition: read,
    saveSectionAudition: vi.fn(async (_task: string, _section: string, input: SectionAuditionInput) => {
      fixture.chosen = input.preview_run_id !== null;
      return read();
    }),
  };
});
it("adopts the played take on a confirmed section and can release the choice after reopening", async () => {
  const script: CreationTaskScript = {
    task_id: "work", id: "script", run_id: "run", topic_id: "topic", title: "企业片", language: "en-US",
    created_at: fixture.revision, updated_at: fixture.revision, confirmed_at: fixture.revision,
    draft_complete: true, selection_status: "confirmed", sections_loaded: false, sections: null, sections_total_count: 1,
  };
  async function open() {
    const view = render(<NarrationAuditionPicker script={script} disabled={false} />);
    fireEvent.click(screen.getByText("采用段落试听"));
    fireEvent.change(await screen.findByLabelText("文案段落"), { target: { value: "opening" } });
    return view;
  }
  const view = await open();
  expect(await screen.findByLabelText("试听录音 1")).toHaveAttribute("src", "/takes/take-2.wav");
  fireEvent.click(screen.getByRole("button", { name: "采用这次试听" }));
  await screen.findByText("已采用为本段配音");
  expect(saveSectionAudition).toHaveBeenCalledWith("work", "opening", {
    request_id: expect.any(String), preview_run_id: "take-2", expected_preview_run_id: null, expected_output_sha256: fixture.candidate.output_sha256,
    expected_script_id: "script", expected_script_updated_at: fixture.revision,
  });
  view.unmount();
  await open();
  fireEvent.click(await screen.findByRole("button", { name: "取消本段采用" }));
  await screen.findByRole("button", { name: "采用这次试听" });
  expect(saveSectionAudition).toHaveBeenLastCalledWith("work", "opening", expect.objectContaining({
    expected_preview_run_id: "take-2", preview_run_id: null,
  }));
});
