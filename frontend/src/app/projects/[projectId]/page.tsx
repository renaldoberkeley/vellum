"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  createDocument,
  getDocumentVersion,
  getProject,
  listDocuments,
  listDocumentVersions,
  restoreDocumentVersion,
  updateDocument,
} from "@/lib/api";
import type { Document, DocumentVersion, DocumentVersionListItem, Project } from "@/lib/types";

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
  const [versionHistory, setVersionHistory] = useState<DocumentVersionListItem[]>([]);
  const [inspectedVersion, setInspectedVersion] = useState<DocumentVersion | null>(null);
  const [loadingVersions, setLoadingVersions] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selectedDocument = useMemo(
    () => documents.find((document) => document.id === selectedDocumentId) ?? null,
    [documents, selectedDocumentId],
  );

  const loadVersionHistory = useCallback(
    async (targetDocumentId: number): Promise<void> => {
      if (Number.isNaN(projectId)) {
        return;
      }
      setLoadingVersions(true);
      try {
        const versions = await listDocumentVersions(projectId, targetDocumentId);
        setVersionHistory(versions);
      } finally {
        setLoadingVersions(false);
      }
    },
    [projectId],
  );

  const onSelectDocument = useCallback(
    (document: Document): void => {
      setSelectedDocumentId(document.id);
      setTitle(document.title);
      setFilename(document.filename);
      setMarkdownContent(document.markdown_content);
      setInspectedVersion(null);
      setMessage(null);
      void loadVersionHistory(document.id).catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Failed to load version history");
      });
    },
    [loadVersionHistory],
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
      onSelectDocument(first);
    }
  }, [onSelectDocument, selectedDocumentId]);

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
      setMessage(null);
      const updated = await updateDocument(projectId, selectedDocumentId, {
        title: title.trim(),
        filename: filename.trim(),
        markdown_content: markdownContent,
      });
      setDocuments((prev) =>
        prev.map((document) => (document.id === updated.id ? updated : document)),
      );
      onSelectDocument(updated);
      setMessage("Document saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save document");
    }
  }

  async function onInspectVersion(versionId: number): Promise<void> {
    if (Number.isNaN(projectId) || selectedDocumentId === null) {
      return;
    }

    try {
      setError(null);
      setMessage(null);
      const version = await getDocumentVersion(projectId, selectedDocumentId, versionId);
      setInspectedVersion(version);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to inspect version");
    }
  }

  async function onRestoreVersion(versionId: number): Promise<void> {
    if (Number.isNaN(projectId) || selectedDocumentId === null) {
      return;
    }

    const confirmed = window.confirm("Restore this version? Current content will be replaced.");
    if (!confirmed) {
      return;
    }

    try {
      setError(null);
      const restored = await restoreDocumentVersion(projectId, selectedDocumentId, versionId);
      setDocuments((prev) =>
        prev.map((document) => (document.id === restored.id ? restored : document)),
      );
      onSelectDocument(restored);
      setMessage(`Restored version ${versionId} successfully.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to restore version");
    }
  }

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <Link href="/">Back to projects</Link>
        <h1>{project ? project.name : "Project"}</h1>
      </header>

      {message ? <p className={styles.message}>{message}</p> : null}
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

          {selectedDocument ? (
            <section className={styles.versionsPanel}>
              <h3>Version History</h3>
              <p className={styles.currentVersion}>Current version: {selectedDocument.current_version}</p>
              {loadingVersions ? <p>Loading versions...</p> : null}
              {!loadingVersions && versionHistory.length === 0 ? <p>No previous versions yet.</p> : null}
              <ul className={styles.versionsList}>
                {versionHistory.map((version) => (
                  <li key={version.id}>
                    <div className={styles.versionMeta}>
                      <span>v{version.version_number}</span>
                      <span>{new Date(version.created_at).toLocaleString()}</span>
                      <span>{version.change_source}</span>
                    </div>
                    <div className={styles.versionActions}>
                      <button type="button" onClick={() => void onInspectVersion(version.id)}>
                        Inspect
                      </button>
                      <button type="button" onClick={() => void onRestoreVersion(version.id)}>
                        Restore
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
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

          {inspectedVersion ? (
            <section className={styles.inspectedVersion}>
              <h3>Inspected Version v{inspectedVersion.version_number}</h3>
              <p>
                {new Date(inspectedVersion.created_at).toLocaleString()} - {inspectedVersion.change_source}
              </p>
              <div className={styles.markdownPreview}>
                <ReactMarkdown>{inspectedVersion.markdown_content || "_No content._"}</ReactMarkdown>
              </div>
            </section>
          ) : null}
        </section>
      </section>
    </main>
  );
}
