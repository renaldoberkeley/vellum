"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  createProjectConversation,
  createDocument,
  downloadDocumentMarkdown,
  getDocument,
  getDocumentVersion,
  getProject,
  importMarkdownDocuments,
  listConversationMessages,
  listDocuments,
  listDocumentVersions,
  listProjectConversations,
  restoreDocumentVersion,
  searchProjectDocuments,
  streamConversationMessage,
  updateDocument,
} from "@/lib/api";
import type {
  ChatMessage,
  Conversation,
  Document,
  DocumentVersion,
  DocumentVersionListItem,
  MarkdownImportFailure,
  Project,
  ProjectSearchResult,
} from "@/lib/types";

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
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<ProjectSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [importFailures, setImportFailures] = useState<MarkdownImportFailure[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<number | null>(null);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatStreaming, setChatStreaming] = useState(false);
  const [chatMeta, setChatMeta] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const importInputRef = useRef<HTMLInputElement | null>(null);

  const selectedDocument = useMemo(
    () => documents.find((document) => document.id === selectedDocumentId) ?? null,
    [documents, selectedDocumentId],
  );

  const activeConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === activeConversationId) ?? null,
    [conversations, activeConversationId],
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
      setImportFailures([]);
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

  const refreshConversationMessages = useCallback(
    async (targetProjectId: number, conversationId: number): Promise<void> => {
      const messages = await listConversationMessages(targetProjectId, conversationId);
      setChatMessages(messages);
    },
    [],
  );

  const ensureProjectConversation = useCallback(async (targetProjectId: number): Promise<number> => {
    const existing = await listProjectConversations(targetProjectId);
    if (existing.length > 0) {
      setConversations(existing);
      setActiveConversationId(existing[0].id);
      await refreshConversationMessages(targetProjectId, existing[0].id);
      return existing[0].id;
    }

    const created = await createProjectConversation(targetProjectId, {});
    setConversations([created]);
    setActiveConversationId(created.id);
    await refreshConversationMessages(targetProjectId, created.id);
    return created.id;
  }, [refreshConversationMessages]);

  useEffect(() => {
    if (Number.isNaN(projectId)) {
      return;
    }

    void (async () => {
      try {
        setError(null);
        setChatMeta(null);
        await Promise.all([refreshData(projectId), ensureProjectConversation(projectId)]);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load project");
      }
    })();
  }, [ensureProjectConversation, projectId, refreshData]);

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

  async function onImportMarkdownFiles(event: React.ChangeEvent<HTMLInputElement>): Promise<void> {
    if (Number.isNaN(projectId)) {
      return;
    }

    const selectedFiles = event.target.files;
    if (!selectedFiles || selectedFiles.length === 0) {
      return;
    }

    const files = Array.from(selectedFiles);
    event.target.value = "";

    try {
      setError(null);
      setMessage(null);
      setImportFailures([]);
      const result = await importMarkdownDocuments(projectId, files);
      await refreshData(projectId);
      setImportFailures(result.failures);
      setMessage(`Imported ${result.imported_count} file(s); ${result.failed_count} failed.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to import markdown files");
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

  async function onDownloadSelectedDocument(): Promise<void> {
    if (Number.isNaN(projectId) || selectedDocumentId === null) {
      return;
    }

    try {
      setError(null);
      const { blob, filename: downloadFilename } = await downloadDocumentMarkdown(projectId, selectedDocumentId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = downloadFilename;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to download markdown file");
    }
  }

  async function onSearchProject(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (Number.isNaN(projectId)) {
      return;
    }

    const query = searchQuery.trim();
    if (!query) {
      setError("Search query cannot be empty");
      return;
    }

    try {
      setError(null);
      setSearching(true);
      const results = await searchProjectDocuments(projectId, query);
      setSearchResults(results);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to search project documents");
    } finally {
      setSearching(false);
    }
  }

  async function onSendChatMessage(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (Number.isNaN(projectId)) {
      return;
    }

    const content = chatInput.trim();
    if (!content) {
      return;
    }

    try {
      let conversationId = activeConversationId;
      if (conversationId === null) {
        conversationId = await ensureProjectConversation(projectId);
      }
      if (conversationId === null) {
        throw new Error("No conversation available");
      }

      const tempUserId = -Date.now();
      const tempAssistantId = tempUserId - 1;

      setError(null);
      setChatStreaming(true);
      setChatMeta(null);
      setChatInput("");

      setChatMessages((prev) => [
        ...prev,
        {
          id: tempUserId,
          conversation_id: conversationId,
          role: "user",
          content,
          created_at: new Date().toISOString(),
        },
        {
          id: tempAssistantId,
          conversation_id: conversationId,
          role: "assistant",
          content: "",
          created_at: new Date().toISOString(),
        },
      ]);

      await streamConversationMessage(
        projectId,
        conversationId,
        { content, selected_document_id: selectedDocumentId },
        {
          onChunk: (chunk) => {
            setChatMessages((prev) =>
              prev.map((msg) =>
                msg.id === tempAssistantId
                  ? {
                      ...msg,
                      content: `${msg.content}${chunk}`,
                    }
                  : msg,
              ),
            );
          },
          onDone: (meta) => {
            if (meta.used_document_ids && meta.used_document_ids.length > 0) {
              setChatMeta(
                `Context docs: ${meta.used_document_ids.join(", ")}${meta.truncated ? " (truncated)" : ""}`,
              );
            } else if (meta.truncated) {
              setChatMeta("Context was truncated to fit model budget.");
            }
          },
        },
      );
      await refreshConversationMessages(projectId, conversationId);
      const refreshedConversations = await listProjectConversations(projectId);
      setConversations(refreshedConversations);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send chat message");
      if (activeConversationId !== null) {
        await refreshConversationMessages(projectId, activeConversationId);
      }
    } finally {
      setChatStreaming(false);
    }
  }

  async function onOpenSearchResult(result: ProjectSearchResult): Promise<void> {
    if (Number.isNaN(projectId)) {
      return;
    }

    const existingDocument = documents.find((document) => document.id === result.document_id);
    if (existingDocument) {
      onSelectDocument(existingDocument);
      return;
    }

    try {
      setError(null);
      const loadedDocument = await getDocument(projectId, result.document_id);
      setDocuments((prev) => {
        if (prev.some((document) => document.id === loadedDocument.id)) {
          return prev;
        }
        return [loadedDocument, ...prev];
      });
      onSelectDocument(loadedDocument);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to open search result");
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
          <button type="button" onClick={() => importInputRef.current?.click()}>
            Import Markdown
          </button>
          <input
            ref={importInputRef}
            type="file"
            accept=".md,.markdown,text/markdown"
            multiple
            className={styles.hiddenInput}
            onChange={(event) => void onImportMarkdownFiles(event)}
          />

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

          {importFailures.length > 0 ? (
            <section className={styles.importFailures}>
              <h3>Import Failures</h3>
              <ul>
                {importFailures.map((failure) => (
                  <li key={`${failure.filename}-${failure.code}`}>
                    <strong>{failure.filename}</strong>: {failure.detail}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <section className={styles.searchPanel}>
            <h3>Project Search</h3>
            <form onSubmit={(event) => void onSearchProject(event)} className={styles.searchForm}>
              <input
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="Search title, filename, or content"
              />
              <button type="submit" disabled={searching}>
                {searching ? "Searching..." : "Search"}
              </button>
            </form>
            {searchResults.length > 0 ? (
              <ul className={styles.searchResults}>
                {searchResults.map((result) => (
                  <li key={`${result.document_id}-${result.filename}-${result.relevance}`}>
                    <button type="button" onClick={() => void onOpenSearchResult(result)}>
                      <strong>{result.filename}</strong>
                      <span>{result.snippet}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </section>

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
              <button type="button" onClick={() => void onDownloadSelectedDocument()}>
                Download Markdown
              </button>
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

        <aside className={styles.chatPanel}>
          <div className={styles.chatHeader}>
            <h3>Project Chat</h3>
            <p>{activeConversation?.title ?? "General conversation"}</p>
          </div>

          <div className={styles.chatHistory}>
            {chatMessages.length === 0 ? <p>No messages yet. Ask about this project.</p> : null}
            {chatMessages.map((chatMessage) => (
              <article
                key={chatMessage.id}
                className={`${styles.chatMessage} ${
                  chatMessage.role === "user" ? styles.chatUserMessage : styles.chatAssistantMessage
                }`}
              >
                <header>{chatMessage.role === "user" ? "You" : "Assistant"}</header>
                <p>{chatMessage.content || (chatMessage.role === "assistant" ? "..." : "")}</p>
              </article>
            ))}
          </div>

          <form onSubmit={(event) => void onSendChatMessage(event)} className={styles.chatForm}>
            <textarea
              value={chatInput}
              onChange={(event) => setChatInput(event.target.value)}
              rows={4}
              placeholder="Ask a question about this project"
              disabled={chatStreaming}
            />
            <button type="submit" disabled={chatStreaming || !chatInput.trim()}>
              {chatStreaming ? "Thinking..." : "Send"}
            </button>
          </form>

          {chatMeta ? <p className={styles.chatMeta}>{chatMeta}</p> : null}
        </aside>
      </section>
    </main>
  );
}
