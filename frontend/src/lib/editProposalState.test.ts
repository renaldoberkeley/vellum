import { describe, expect, it } from "vitest";

import type { Document } from "./types";
import {
  applyEditProposalChunk,
  applyEditProposalDone,
  applyEditProposalError,
  canAcceptEditProposal,
  createInitialEditProposal,
} from "./editProposalState";

function makeDocument(): Document {
  const now = new Date().toISOString();
  return {
    id: 4,
    project_id: 1,
    title: "Technical Architecture",
    filename: "4. technical-architecture.md",
    markdown_content: "# Original\n\nBody",
    current_version: 3,
    created_at: now,
    updated_at: now,
  };
}

describe("edit proposal stream state", () => {
  it("keeps partial chunks visible and marks proposal incomplete on output_truncated", () => {
    const initial = createInitialEditProposal(makeDocument(), "Update architecture");
    const withChunks = applyEditProposalChunk(applyEditProposalChunk(initial, "# Revised\n"), "\nBody");

    const withError = applyEditProposalError(withChunks, {
      code: "output_truncated",
      detail: "The proposed edit exceeded the model output limit and is incomplete.",
    });

    expect(withError.proposed_markdown_content).toContain("# Revised");
    expect(withError.status).toBe("incomplete");
    expect(withError.done_received).toBe(false);
    expect(canAcceptEditProposal(withError, false)).toBe(false);
  });

  it("enables acceptance only after successful done with non-empty content and known base version", () => {
    const initial = createInitialEditProposal(makeDocument(), "Update architecture");
    const withChunk = applyEditProposalChunk(initial, "# Revised\n\nBody");

    expect(canAcceptEditProposal(withChunk, false)).toBe(false);

    const completed = applyEditProposalDone(withChunk, {
      document_id: 4,
      base_version: 3,
      used_document_ids: [4, 2],
      used_document_filenames: ["4. technical-architecture.md", "2. product-requirements.md"],
      context_documents: [
        { filename: "4. technical-architecture.md", reason: "selected" },
        { filename: "2. product-requirements.md", reason: "explicit_reference" },
      ],
      truncated: false,
    });

    expect(completed.status).toBe("complete");
    expect(completed.done_received).toBe(true);
    expect(canAcceptEditProposal(completed, false)).toBe(true);
    expect(canAcceptEditProposal(completed, true)).toBe(false);
  });
});
