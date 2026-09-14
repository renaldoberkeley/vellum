import type {
  ChatMessage,
  ContextDocumentDiagnostic,
  Conversation,
  Document,
  DocumentVersion,
  DocumentVersionListItem,
  MarkdownImportResponse,
  Project,
  ProjectSearchResult,
} from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const fallback = `Request failed with status ${response.status}`;
    let message = fallback;
    try {
      const payload = (await response.json()) as { detail?: string };
      message = payload.detail ?? fallback;
    } catch {
      message = fallback;
    }
    throw new Error(message);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export async function listProjects(): Promise<Project[]> {
  const response = await fetch(`${API_BASE}/api/projects`, { cache: "no-store" });
  return parseResponse<Project[]>(response);
}

export async function createProject(input: {
  name: string;
  description?: string;
}): Promise<Project> {
  const response = await fetch(`${API_BASE}/api/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return parseResponse<Project>(response);
}

export async function getProject(projectId: number): Promise<Project> {
  const response = await fetch(`${API_BASE}/api/projects/${projectId}`, { cache: "no-store" });
  return parseResponse<Project>(response);
}

export async function listDocuments(projectId: number): Promise<Document[]> {
  const response = await fetch(`${API_BASE}/api/projects/${projectId}/documents`, {
    cache: "no-store",
  });
  return parseResponse<Document[]>(response);
}

export async function createDocument(
  projectId: number,
  input: { title: string; filename: string; markdown_content: string },
): Promise<Document> {
  const response = await fetch(`${API_BASE}/api/projects/${projectId}/documents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return parseResponse<Document>(response);
}

export async function getDocument(projectId: number, documentId: number): Promise<Document> {
  const response = await fetch(`${API_BASE}/api/projects/${projectId}/documents/${documentId}`, {
    cache: "no-store",
  });
  return parseResponse<Document>(response);
}

export async function updateDocument(
  projectId: number,
  documentId: number,
  input: { title?: string; filename?: string; markdown_content?: string },
): Promise<Document> {
  const response = await fetch(`${API_BASE}/api/projects/${projectId}/documents/${documentId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return parseResponse<Document>(response);
}

export async function deleteDocument(projectId: number, documentId: number): Promise<void> {
  const response = await fetch(`${API_BASE}/api/projects/${projectId}/documents/${documentId}`, {
    method: "DELETE",
  });
  return parseResponse<void>(response);
}

export async function listDocumentVersions(
  projectId: number,
  documentId: number,
): Promise<DocumentVersionListItem[]> {
  const response = await fetch(
    `${API_BASE}/api/projects/${projectId}/documents/${documentId}/versions`,
    { cache: "no-store" },
  );
  return parseResponse<DocumentVersionListItem[]>(response);
}

export async function getDocumentVersion(
  projectId: number,
  documentId: number,
  versionId: number,
): Promise<DocumentVersion> {
  const response = await fetch(
    `${API_BASE}/api/projects/${projectId}/documents/${documentId}/versions/${versionId}`,
    { cache: "no-store" },
  );
  return parseResponse<DocumentVersion>(response);
}

export async function restoreDocumentVersion(
  projectId: number,
  documentId: number,
  versionId: number,
): Promise<Document> {
  const response = await fetch(
    `${API_BASE}/api/projects/${projectId}/documents/${documentId}/versions/${versionId}/restore`,
    { method: "POST" },
  );
  return parseResponse<Document>(response);
}

export async function importMarkdownDocuments(
  projectId: number,
  files: File[],
): Promise<MarkdownImportResponse> {
  const formData = new FormData();
  for (const file of files) {
    formData.append("files", file);
  }

  const response = await fetch(`${API_BASE}/api/projects/${projectId}/import/markdown`, {
    method: "POST",
    body: formData,
  });
  return parseResponse<MarkdownImportResponse>(response);
}

export async function searchProjectDocuments(
  projectId: number,
  query: string,
): Promise<ProjectSearchResult[]> {
  const params = new URLSearchParams({ q: query });
  const response = await fetch(`${API_BASE}/api/projects/${projectId}/search?${params.toString()}`, {
    cache: "no-store",
  });
  return parseResponse<ProjectSearchResult[]>(response);
}

export async function downloadDocumentMarkdown(
  projectId: number,
  documentId: number,
): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(
    `${API_BASE}/api/projects/${projectId}/documents/${documentId}/export/markdown`,
  );

  if (!response.ok) {
    const fallback = `Request failed with status ${response.status}`;
    let message = fallback;
    try {
      const payload = (await response.json()) as { detail?: string };
      message = payload.detail ?? fallback;
    } catch {
      message = fallback;
    }
    throw new Error(message);
  }

  const contentDisposition = response.headers.get("content-disposition") ?? "";
  const filenameMatch = /filename="?([^";]+)"?/i.exec(contentDisposition);
  const filename = filenameMatch?.[1] ?? "document.md";
  const blob = await response.blob();

  return { blob, filename };
}

export async function listProjectConversations(projectId: number): Promise<Conversation[]> {
  const response = await fetch(`${API_BASE}/api/projects/${projectId}/conversations`, {
    cache: "no-store",
  });
  return parseResponse<Conversation[]>(response);
}

export async function createProjectConversation(
  projectId: number,
  input: { title?: string | null } = {},
): Promise<Conversation> {
  const response = await fetch(`${API_BASE}/api/projects/${projectId}/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return parseResponse<Conversation>(response);
}

export async function listConversationMessages(
  projectId: number,
  conversationId: number,
): Promise<ChatMessage[]> {
  const response = await fetch(
    `${API_BASE}/api/projects/${projectId}/conversations/${conversationId}/messages`,
    { cache: "no-store" },
  );
  return parseResponse<ChatMessage[]>(response);
}

type StreamHandlers = {
  onChunk: (chunk: string) => void;
  onDone?: (meta: StreamDoneMeta) => void;
};

export class StreamError extends Error {
  code?: string;

  constructor(message: string, code?: string) {
    super(message);
    this.code = code;
  }
}

type StreamDoneMeta = {
  title?: string;
  filename?: string;
  document_id?: number;
  base_version?: number;
  used_document_ids?: number[];
  used_document_filenames?: string[];
  context_documents?: ContextDocumentDiagnostic[];
  truncated?: boolean;
};

type StreamSsePayload = {
  type?: string;
  content?: string;
  code?: string;
  detail?: string;
  title?: string;
  filename?: string;
  document_id?: number;
  base_version?: number;
  used_document_ids?: number[];
  used_document_filenames?: string[];
  context_documents?: ContextDocumentDiagnostic[];
  truncated?: boolean;
};

export function parseSseMessageEvent(event: string): StreamSsePayload | null {
  const dataLine = event
    .split("\n")
    .map((line) => line.trim())
    .find((line) => line.startsWith("data:"));
  if (!dataLine) {
    return null;
  }
  return JSON.parse(dataLine.slice(5).trim()) as StreamSsePayload;
}

async function streamSseRequest(
  input: RequestInfo | URL,
  init: RequestInit,
  handlers: StreamHandlers,
): Promise<void> {
  const response = await fetch(input, init);

  if (!response.ok) {
    const fallback = `Request failed with status ${response.status}`;
    let message = fallback;
    try {
      const payload = (await response.json()) as { detail?: string };
      message = payload.detail ?? fallback;
    } catch {
      message = fallback;
    }
    throw new Error(message);
  }

  if (!response.body) {
    throw new Error("No streaming response body available");
  }

  const decoder = new TextDecoder();
  const reader = response.body.getReader();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });

    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";

    for (const event of events) {
        const payload = parseSseMessageEvent(event);
        if (!payload) {
        continue;
      }

      if (payload.type === "chunk" && payload.content) {
        handlers.onChunk(payload.content);
      }
      if (payload.type === "error") {
          throw new StreamError(payload.detail ?? "Streaming failed", payload.code);
      }
      if (payload.type === "done") {
        handlers.onDone?.({
          title: payload.title,
          filename: payload.filename,
            document_id: payload.document_id,
            base_version: payload.base_version,
          used_document_ids: payload.used_document_ids,
          used_document_filenames: payload.used_document_filenames,
          context_documents: payload.context_documents,
          truncated: payload.truncated,
        });
      }
    }
  }
}

export async function streamConversationMessage(
  projectId: number,
  conversationId: number,
  input: { content: string; selected_document_id?: number | null },
  handlers: StreamHandlers,
): Promise<void> {
  await streamSseRequest(
    `${API_BASE}/api/projects/${projectId}/conversations/${conversationId}/messages`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
    handlers,
  );
}

export async function streamGenerateDocumentProposal(
  projectId: number,
  input: {
    instruction: string;
    filename?: string;
    title?: string;
    selected_document_id?: number | null;
  },
  handlers: StreamHandlers,
): Promise<void> {
  await streamSseRequest(
    `${API_BASE}/api/projects/${projectId}/ai/generate-document`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
    handlers,
  );
}

export async function acceptGeneratedDocumentProposal(
  projectId: number,
  input: { title: string; filename: string; markdown_content: string },
): Promise<Document> {
  const response = await fetch(`${API_BASE}/api/projects/${projectId}/ai/generated-document/accept`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return parseResponse<Document>(response);
}

export async function streamProposeDocumentEdit(
  projectId: number,
  documentId: number,
  input: { instruction: string },
  handlers: StreamHandlers,
): Promise<void> {
  await streamSseRequest(
    `${API_BASE}/api/projects/${projectId}/documents/${documentId}/ai/propose-edit`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
    handlers,
  );
}

export async function acceptDocumentEditProposal(
  projectId: number,
  documentId: number,
  input: { base_version: number; markdown_content: string; instruction?: string },
): Promise<Document> {
  const response = await fetch(`${API_BASE}/api/projects/${projectId}/documents/${documentId}/ai/accept-edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return parseResponse<Document>(response);
}
