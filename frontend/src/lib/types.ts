export type Project = {
  id: number;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
};

export type Document = {
  id: number;
  project_id: number;
  title: string;
  filename: string;
  markdown_content: string;
  current_version: number;
  created_at: string;
  updated_at: string;
};

export type DocumentVersionListItem = {
  id: number;
  document_id: number;
  version_number: number;
  change_source: "manual" | "ai" | "restore" | "import" | string;
  change_summary: string | null;
  created_at: string;
};

export type DocumentVersion = DocumentVersionListItem & {
  title: string;
  filename: string;
  markdown_content: string;
};

export type MarkdownImportFailure = {
  filename: string;
  detail: string;
  code: string;
};

export type MarkdownImportResponse = {
  imported: Document[];
  failures: MarkdownImportFailure[];
  imported_count: number;
  failed_count: number;
};

export type ProjectSearchResult = {
  document_id: number;
  title: string;
  filename: string;
  snippet: string;
  relevance: number;
};

export type Conversation = {
  id: number;
  project_id: number;
  title: string | null;
  created_at: string;
  updated_at: string;
};

export type ChatMessage = {
  id: number;
  conversation_id: number;
  role: string;
  content: string;
  created_at: string;
};
