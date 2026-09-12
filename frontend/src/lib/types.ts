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
