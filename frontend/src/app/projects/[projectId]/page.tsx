"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import { useCallback, useEffect, useMemo, useState } from "react";

import { createDocument, getProject, listDocuments, updateDocument } from "@/lib/api";
import type { Document, Project } from "@/lib/types";

import styles from "./project.module.css";

export default function ProjectPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = Number.parseInt(params.projectId, 10);
  const [project, setProject] = useState<Project | null>(null);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [selectedDocumentId, setSelectedDocumentId] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [filename, setFilename] = useState("");
  const [markdownContent, setMarkdownContent] = useState("");
  const [error, setError] = useState<string | null>(null);

  const selectedDocument = useMemo(
    () => documents.find((document) => document.id === selectedDocumentId) ?? null,
    [documents, selectedDocumentId],
  );

  const refreshData = useCallback(async (targetProjectId: number): Promise<void> => {
    const [projectData, documentsData] = await Promise.all([
      getProject(targetProjectId),
      listDocuments(targetProjectId),
    ]);
    setProject(projectData);
    setDocuments(documentsData);

    if (documentsData.length > 0 && selectedDocumentId === null) {
      const first = documentsData[0];
      setSelectedDocumentId(first.id);
      setTitle(first.title);
      setFilename(first.filename);
      setMarkdownContent(first.markdown_content);
    }
  }, [selectedDocumentId]);

  useEffect(() => {
    if (Number.isNaN(projectId)) {
      return;
    }

    void (async () => {
      try {
        setError(null);
        await refreshData(projectId);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load project");
      }
    })();
  }, [projectId, refreshData]);

  function onSelectDocument(document: Document): void {
    setSelectedDocumentId(document.id);
    setTitle(document.title);
    setFilename(document.filename);
    setMarkdownContent(document.markdown_content);
  }

  async function onCreateDocument(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (Number.isNaN(projectId)) {
      return;
    }

    const baseName = `document-${Date.now()}`;

    try {
      setError(null);
      const created = await createDocument(projectId, {
        title: "Untitled",
        filename: `${baseName}.md`,
        markdown_content: "",
      });
      await refreshData(projectId);
      onSelectDocument(created);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create document");
    }
  }

  async function onSaveDocument(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (selectedDocumentId === null || !title.trim() || !filename.trim()) {
      return;
    }

    try {
      setError(null);
      const updated = await updateDocument(projectId, selectedDocumentId, {
        title: title.trim(),
        filename: filename.trim(),
        markdown_content: markdownContent,
      });
      setDocuments((prev) =>
        prev.map((document) => (document.id === updated.id ? updated : document)),
      );
      onSelectDocument(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save document");
    }
  }

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <Link href="/">Back to projects</Link>
        <h1>{project ? project.name : "Project"}</h1>
      </header>

      {error ? <p className={styles.error}>{error}</p> : null}

      <section className={styles.workspace}>
        <aside className={styles.sidebar}>
          <form onSubmit={(event) => void onCreateDocument(event)}>
            <button type="submit">New document</button>
          </form>

          <ul>
            {documents.map((document) => (
              <li key={document.id}>
                <button
                  type="button"
                  className={selectedDocumentId === document.id ? styles.active : ""}
                  onClick={() => onSelectDocument(document)}
                >
                  {document.filename}
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <section className={styles.editor}>
          {selectedDocument ? (
            <form onSubmit={(event) => void onSaveDocument(event)} className={styles.editorForm}>
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Title"
              />
              <input
                value={filename}
                onChange={(event) => setFilename(event.target.value)}
                placeholder="filename.md"
              />
              <div className={styles.editorGrid}>
                <div className={styles.editorPane}>
                  <h3>Markdown</h3>
                  <textarea
                    value={markdownContent}
                    onChange={(event) => setMarkdownContent(event.target.value)}
                    rows={20}
                  />
                </div>
                <div className={styles.previewPane}>
                  <h3>Preview</h3>
                  <div className={styles.markdownPreview}>
                    <ReactMarkdown>{markdownContent || "_No content yet._"}</ReactMarkdown>
                  </div>
                </div>
              </div>
              <button type="submit">Save</button>
            </form>
          ) : (
            <p>Create or select a document to begin editing.</p>
          )}
        </section>
      </section>
    </main>
  );
}
