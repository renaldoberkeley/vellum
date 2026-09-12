import type { Document, DocumentVersion, DocumentVersionListItem, Project } from "@/lib/types";

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
