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
  created_at: string;
  updated_at: string;
};
