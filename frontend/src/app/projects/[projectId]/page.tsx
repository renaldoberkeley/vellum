"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  acceptDocumentEditProposal,
  acceptGeneratedDocumentProposal,
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
  streamGenerateDocumentProposal,
  streamProposeDocumentEdit,
  StreamError,
  streamConversationMessage,
  updateDocument,
} from "@/lib/api";
import {
  applyEditProposalChunk,
  applyEditProposalDone,
  applyEditProposalError,
  canAcceptEditProposal as canAcceptEditProposalState,
  createInitialEditProposal,
} from "@/lib/editProposalState";
import type {
  ChatMessage,
  ContextDocumentDiagnostic,
  Conversation,
  DocumentEditProposal,
  Document,
  DocumentVersion,
  DocumentVersionListItem,
  GeneratedDocumentProposal,
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
  const [generationInstruction, setGenerationInstruction] = useState("");
  const [generationTitle, setGenerationTitle] = useState("");
  const [generationFilename, setGenerationFilename] = useState("");
  const [generationUseSelectedHint, setGenerationUseSelectedHint] = useState(false);
  const [generationStreaming, setGenerationStreaming] = useState(false);
  const [generatedProposal, setGeneratedProposal] = useState<GeneratedDocumentProposal | null>(null);
  const [editInstruction, setEditInstruction] = useState("");
  const [editStreaming, setEditStreaming] = useState(false);
  const [editProposal, setEditProposal] = useState<DocumentEditProposal | null>(null);
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

  const generationMeta = useMemo(() => {
    if (!generatedProposal) {
      return null;
    }

    return formatContextDiagnostics({
      used_document_ids: generatedProposal.used_document_ids,
      used_document_filenames: generatedProposal.used_document_filenames,
      context_documents: generatedProposal.context_documents,
      truncated: generatedProposal.truncated,
    });
  }, [generatedProposal]);

  const editMeta = useMemo(() => {
    if (!editProposal) {
      return null;
    }

    return formatContextDiagnostics({
      used_document_ids: editProposal.used_document_ids,
      used_document_filenames: editProposal.used_document_filenames,
      context_documents: editProposal.context_documents,
      truncated: editProposal.truncated,
    });
  }, [editProposal]);

  const canAcceptEditProposal = useMemo(
    () => canAcceptEditProposalState(editProposal, editStreaming),
    [editProposal, editStreaming],
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
      setEditProposal(null);
      setEditInstruction("");
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
            setChatMeta(formatContextDiagnostics(meta));
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

  async function onGenerateDocumentProposal(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (Number.isNaN(projectId)) {
      return;
    }

    const instruction = generationInstruction.trim();
    if (!instruction) {
      setError("Generation instruction cannot be empty");
      return;
    }

    try {
      setError(null);
      setMessage(null);
      setGenerationStreaming(true);

      setGeneratedProposal({
        title: generationTitle.trim() || "Generated Document",
        filename: generationFilename.trim() || "generated-document.md",
        markdown_content: "",
        used_document_ids: [],
        used_document_filenames: [],
        context_documents: [],
        truncated: false,
      });

      await streamGenerateDocumentProposal(
        projectId,
        {
          instruction,
          title: generationTitle.trim() || undefined,
          filename: generationFilename.trim() || undefined,
          selected_document_id: generationUseSelectedHint ? selectedDocumentId : null,
        },
        {
          onChunk: (chunk) => {
            setGeneratedProposal((prev) =>
              prev
                ? {
                    ...prev,
                    markdown_content: `${prev.markdown_content}${chunk}`,
                  }
                : prev,
            );
          },
          onDone: (meta) => {
            setGeneratedProposal((prev) =>
              prev
                ? {
                    ...prev,
                    title: meta.title ?? prev.title,
                    filename: meta.filename ?? prev.filename,
                    used_document_ids: meta.used_document_ids ?? [],
                    used_document_filenames: meta.used_document_filenames ?? [],
                    context_documents: meta.context_documents ?? [],
                    truncated: Boolean(meta.truncated),
                  }
                : prev,
            );
          },
        },
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to generate proposal");
      setGeneratedProposal(null);
    } finally {
      setGenerationStreaming(false);
    }
  }

  async function onAcceptGeneratedDocument(): Promise<void> {
    if (Number.isNaN(projectId) || !generatedProposal) {
      return;
    }

    try {
      setError(null);
      const created = await acceptGeneratedDocumentProposal(projectId, {
        title: generatedProposal.title.trim(),
        filename: generatedProposal.filename.trim(),
        markdown_content: generatedProposal.markdown_content,
      });
      await refreshData(projectId);
      onSelectDocument(created);
      setGeneratedProposal(null);
      setGenerationInstruction("");
      setGenerationTitle("");
      setGenerationFilename("");
      setMessage(`Accepted proposal and created ${created.filename}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to accept proposal");
    }
  }

  function onDiscardGeneratedDocument(): void {
    setGeneratedProposal(null);
    setMessage("Discarded generated proposal.");
  }

  async function onProposeEdit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (Number.isNaN(projectId) || !selectedDocument) {
      return;
    }

    const instruction = editInstruction.trim();
    if (!instruction) {
      setError("AI edit instruction cannot be empty");
      return;
    }

    try {
      setError(null);
      setMessage(null);
      setEditStreaming(true);
      setEditProposal(createInitialEditProposal(selectedDocument, instruction));

      await streamProposeDocumentEdit(
        projectId,
        selectedDocument.id,
        { instruction },
        {
          onChunk: (chunk) => {
            setEditProposal((prev) =>
              prev
                ? applyEditProposalChunk(prev, chunk)
                : prev,
            );
          },
          onDone: (meta) => {
            setEditProposal((prev) =>
              prev
                ? applyEditProposalDone(prev, meta)
                : prev,
            );
          },
        },
      );
    } catch (err) {
      const streamError = err instanceof StreamError ? err : null;
      const detail = streamError?.message ?? (err instanceof Error ? err.message : "Failed to generate edit proposal");
      const errorCode = streamError?.code;

      setError(detail);
      setEditProposal((prev) =>
        prev
          ? applyEditProposalError(prev, { code: errorCode, detail })
          : prev,
      );
    } finally {
      setEditStreaming(false);
    }
  }

  async function onAcceptEditProposal(): Promise<void> {
    if (Number.isNaN(projectId) || !selectedDocument || !editProposal) {
      return;
    }

    try {
      setError(null);
      const updated = await acceptDocumentEditProposal(projectId, selectedDocument.id, {
        base_version: editProposal.base_version,
        markdown_content: editProposal.proposed_markdown_content,
        instruction: editProposal.instruction,
      });
      await refreshData(projectId);
      onSelectDocument(updated);
      setEditProposal(null);
      setEditInstruction("");
      setMessage(`Accepted AI edit for ${updated.filename}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to accept edit proposal");
    }
  }

  function onDiscardEditProposal(): void {
    setEditProposal(null);
    setMessage("Discarded AI edit proposal.");
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
          <section className={styles.editPanel}>
            <h3>AI Edit Selected Document</h3>
            {selectedDocument ? <p className={styles.editTarget}>Target: {selectedDocument.filename}</p> : null}
            <form onSubmit={(event) => void onProposeEdit(event)} className={styles.editForm}>
              <textarea
                value={editInstruction}
                onChange={(event) => setEditInstruction(event.target.value)}
                rows={4}
                placeholder="Describe the change to apply to the selected document"
                disabled={editStreaming || !selectedDocument}
              />
              <button type="submit" disabled={editStreaming || !selectedDocument || !editInstruction.trim()}>
                {editStreaming ? "Generating Edit..." : "Generate Proposal"}
              </button>
            </form>

            {editProposal ? (
              <section className={styles.editDraft}>
                <p className={styles.generatedBadge}>Draft / Proposed Edit</p>
                <p className={styles.editVersion}>Base version: v{editProposal.base_version}</p>
                {editProposal.status === "incomplete" ? (
                  <p className={styles.editIncomplete}>Incomplete proposal: output exceeded model limit. Regenerate before accepting.</p>
                ) : null}
                {editProposal.status === "error" && editProposal.error_detail ? (
                  <p className={styles.editIncomplete}>Proposal failed: {editProposal.error_detail}</p>
                ) : null}

                <div className={styles.editReviewGrid}>
                  <div className={styles.editReviewPane}>
                    <h4>Current Content</h4>
                    <div className={styles.markdownPreview}>
                      <ReactMarkdown>{editProposal.current_markdown_content || "_No content yet._"}</ReactMarkdown>
                    </div>
                  </div>

                  <div className={styles.editReviewPane}>
                    <h4>Proposed Content</h4>
                    <textarea
                      value={editProposal.proposed_markdown_content}
                      onChange={(event) =>
                        setEditProposal((prev) =>
                          prev
                            ? {
                                ...prev,
                                proposed_markdown_content: event.target.value,
                              }
                            : prev,
                        )
                      }
                      rows={14}
                      disabled={editStreaming}
                    />
                  </div>

                  <div className={styles.editReviewPane}>
                    <h4>Line Diff</h4>
                    <pre className={styles.diffBlock}>
                      {buildLineDiff(editProposal.current_markdown_content, editProposal.proposed_markdown_content).map(
                        (line, index) => (
                          <div
                            key={`${line.type}-${index}-${line.content}`}
                            className={
                              line.type === "added"
                                ? styles.diffAdded
                                : line.type === "removed"
                                  ? styles.diffRemoved
                                  : styles.diffUnchanged
                            }
                          >
                            {line.type === "added" ? "+ " : line.type === "removed" ? "- " : "  "}
                            {line.content || " "}
                          </div>
                        ),
                      )}
                    </pre>
                  </div>
                </div>

                {editMeta ? <p className={styles.chatMeta}>{editMeta}</p> : null}

                <div className={styles.generatedActions}>
                  <button type="button" onClick={() => void onAcceptEditProposal()} disabled={!canAcceptEditProposal}>
                    Accept Changes
                  </button>
                  <button type="button" onClick={onDiscardEditProposal} disabled={editStreaming}>
                    Discard
                  </button>
                </div>
              </section>
            ) : null}
          </section>

          <section className={styles.generationPanel}>
            <h3>Generate Document</h3>
            <form onSubmit={(event) => void onGenerateDocumentProposal(event)} className={styles.generationForm}>
              <textarea
                value={generationInstruction}
                onChange={(event) => setGenerationInstruction(event.target.value)}
                rows={4}
                placeholder="Describe the document you want to generate"
                disabled={generationStreaming}
              />
              <input
                value={generationTitle}
                onChange={(event) => setGenerationTitle(event.target.value)}
                placeholder="Optional title"
                disabled={generationStreaming}
              />
              <input
                value={generationFilename}
                onChange={(event) => setGenerationFilename(event.target.value)}
                placeholder="Optional filename (e.g. plan.md)"
                disabled={generationStreaming}
              />
              <label className={styles.generationHintToggle}>
                <input
                  type="checkbox"
                  checked={generationUseSelectedHint}
                  onChange={(event) => setGenerationUseSelectedHint(event.target.checked)}
                  disabled={generationStreaming}
                />
                Use selected document as context hint
              </label>
              <button type="submit" disabled={generationStreaming || !generationInstruction.trim()}>
                {generationStreaming ? "Generating..." : "Generate"}
              </button>
            </form>

            {generatedProposal ? (
              <section className={styles.generatedDraft}>
                <p className={styles.generatedBadge}>Draft / Proposed</p>
                <label>
                  Title
                  <input
                    value={generatedProposal.title}
                    onChange={(event) =>
                      setGeneratedProposal((prev) =>
                        prev
                          ? {
                              ...prev,
                              title: event.target.value,
                            }
                          : prev,
                      )
                    }
                    disabled={generationStreaming}
                  />
                </label>
                <label>
                  Filename
                  <input
                    value={generatedProposal.filename}
                    onChange={(event) =>
                      setGeneratedProposal((prev) =>
                        prev
                          ? {
                              ...prev,
                              filename: event.target.value,
                            }
                          : prev,
                      )
                    }
                    disabled={generationStreaming}
                  />
                </label>

                <div className={styles.generatedDraftGrid}>
                  <div className={styles.generatedEditorPane}>
                    <h4>Draft Markdown</h4>
                    <textarea
                      value={generatedProposal.markdown_content}
                      onChange={(event) =>
                        setGeneratedProposal((prev) =>
                          prev
                            ? {
                                ...prev,
                                markdown_content: event.target.value,
                              }
                            : prev,
                        )
                      }
                      rows={14}
                      disabled={generationStreaming}
                    />
                  </div>
                  <div className={styles.generatedPreviewPane}>
                    <h4>Draft Preview</h4>
                    <div className={styles.markdownPreview}>
                      <ReactMarkdown>{generatedProposal.markdown_content || "_No content yet._"}</ReactMarkdown>
                    </div>
                  </div>
                </div>

                {generationMeta ? <p className={styles.chatMeta}>{generationMeta}</p> : null}

                <div className={styles.generatedActions}>
                  <button type="button" onClick={() => void onAcceptGeneratedDocument()} disabled={generationStreaming}>
                    Accept Document
                  </button>
                  <button type="button" onClick={onDiscardGeneratedDocument} disabled={generationStreaming}>
                    Discard
                  </button>
                </div>
              </section>
            ) : null}
          </section>

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

function formatContextDiagnostics(meta: {
  used_document_ids?: number[];
  used_document_filenames?: string[];
  context_documents?: ContextDocumentDiagnostic[];
  truncated?: boolean;
}): string | null {
  const lines: string[] = ["Context:"];

  if (meta.context_documents && meta.context_documents.length > 0) {
    for (const item of meta.context_documents) {
      lines.push(`- ${item.filename} - ${item.reason}`);
    }
  } else if (meta.used_document_filenames && meta.used_document_filenames.length > 0) {
    for (const filename of meta.used_document_filenames) {
      lines.push(`- ${filename}`);
    }
  } else if (meta.used_document_ids && meta.used_document_ids.length > 0) {
    for (const id of meta.used_document_ids) {
      lines.push(`- ${id}`);
    }
  } else if (!meta.truncated) {
    return null;
  }

  if (meta.truncated) {
    lines.push("Context truncated");
  }

  return lines.join("\n");
}

type DiffLine = {
  type: "unchanged" | "added" | "removed";
  content: string;
};

function buildLineDiff(previousText: string, nextText: string): DiffLine[] {
  const previousLines = previousText.split("\n");
  const nextLines = nextText.split("\n");
  const dp: number[][] = Array.from({ length: previousLines.length + 1 }, () =>
    Array.from({ length: nextLines.length + 1 }, () => 0),
  );

  for (let i = previousLines.length - 1; i >= 0; i -= 1) {
    for (let j = nextLines.length - 1; j >= 0; j -= 1) {
      if (previousLines[i] === nextLines[j]) {
        dp[i][j] = dp[i + 1][j + 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
  }

  const result: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < previousLines.length && j < nextLines.length) {
    if (previousLines[i] === nextLines[j]) {
      result.push({ type: "unchanged", content: previousLines[i] });
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      result.push({ type: "removed", content: previousLines[i] });
      i += 1;
    } else {
      result.push({ type: "added", content: nextLines[j] });
      j += 1;
    }
  }

  while (i < previousLines.length) {
    result.push({ type: "removed", content: previousLines[i] });
    i += 1;
  }
  while (j < nextLines.length) {
    result.push({ type: "added", content: nextLines[j] });
    j += 1;
  }

  return result;
}
