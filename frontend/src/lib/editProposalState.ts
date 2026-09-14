import type { Document, DocumentEditProposal } from "./types";

export function createInitialEditProposal(document: Document, instruction: string): DocumentEditProposal {
  return {
    document_id: document.id,
    base_version: document.current_version,
    current_markdown_content: document.markdown_content,
    proposed_markdown_content: "",
    used_document_ids: [],
    used_document_filenames: [],
    context_documents: [],
    truncated: false,
    instruction,
    done_received: false,
    status: "streaming",
  };
}

export function applyEditProposalChunk(proposal: DocumentEditProposal, chunk: string): DocumentEditProposal {
  return {
    ...proposal,
    proposed_markdown_content: `${proposal.proposed_markdown_content}${chunk}`,
  };
}

export function applyEditProposalDone(
  proposal: DocumentEditProposal,
  meta: {
    document_id?: number;
    base_version?: number;
    used_document_ids?: number[];
    used_document_filenames?: string[];
    context_documents?: DocumentEditProposal["context_documents"];
    truncated?: boolean;
  },
): DocumentEditProposal {
  return {
    ...proposal,
    document_id: meta.document_id ?? proposal.document_id,
    base_version: meta.base_version ?? proposal.base_version,
    used_document_ids: meta.used_document_ids ?? [],
    used_document_filenames: meta.used_document_filenames ?? [],
    context_documents: meta.context_documents ?? [],
    truncated: Boolean(meta.truncated),
    done_received: true,
    status: "complete",
    error_code: undefined,
    error_detail: undefined,
  };
}

export function applyEditProposalError(
  proposal: DocumentEditProposal,
  input: { code?: string; detail: string },
): DocumentEditProposal {
  return {
    ...proposal,
    done_received: false,
    status: input.code === "output_truncated" ? "incomplete" : "error",
    error_code: input.code,
    error_detail: input.detail,
  };
}

export function canAcceptEditProposal(
  proposal: DocumentEditProposal | null,
  isStreaming: boolean,
): boolean {
  if (!proposal || isStreaming) {
    return false;
  }

  return (
    proposal.status === "complete"
    && proposal.done_received
    && proposal.base_version > 0
    && proposal.proposed_markdown_content.trim().length > 0
  );
}
