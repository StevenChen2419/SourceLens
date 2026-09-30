import { useEffect, useRef, useState } from 'react';
import { deleteDocument, listDocuments } from '../api';
import type { DocumentSummary } from '../types';

const stateLabels = { indexed: 'Indexed', indexing: 'Indexing — may be incomplete', failed: 'Ingestion incomplete', deleting: 'Deletion incomplete' };

export function DocumentList({ revision }: { revision: number }) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [selected, setSelected] = useState<DocumentSummary | null>(null);
  const sequence = useRef(0);
  const cancel = useRef<HTMLButtonElement>(null);
  const refreshButton = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef(false);

  async function refresh() {
    const current = ++sequence.current;
    setLoading(true);
    try {
      const result = await listDocuments();
      if (current === sequence.current) { setDocuments(result.documents); setError(''); }
    } catch (error) {
      if (current === sequence.current) setError(error instanceof Error ? error.message : 'Unable to load documents.');
    } finally {
      if (current === sequence.current) setLoading(false);
    }
  }

  useEffect(() => { void refresh(); return () => { sequence.current++; }; }, [revision]);
  useEffect(() => {
    if (selected) cancel.current?.focus();
    else if (restoreFocus.current && !deleting) {
      refreshButton.current?.focus(); restoreFocus.current = false;
    }
  }, [selected, deleting]);

  async function remove() {
    if (!selected || deleting) return;
    ++sequence.current; // Ignore any earlier list request completing during deletion.
    setLoading(false);
    setDeleting(true); setError(''); setNotice('');
    try {
      const result = await deleteDocument(selected.document_id);
      ++sequence.current;
      setLoading(false);
      setDocuments(items => items.filter(item => item.document_id !== selected.document_id));
      setNotice(result.original_retained
        ? 'Document removed from Search. No legacy original PDF was deleted.'
        : 'Document and original PDF deleted.');
      setSelected(null);
      restoreFocus.current = true;
    } catch (error) {
      await refresh(); // Keep partial-deletion state visible, then preserve the error.
      setError(error instanceof Error ? error.message : 'Deletion could not be completed.');
    } finally { setDeleting(false); }
  }

  return <section className="card document-list" aria-labelledby="documents-heading">
    <div className="answer-top"><h2 id="documents-heading">Your documents</h2>
      <button ref={refreshButton} className="button secondary" onClick={() => void refresh()} disabled={loading || deleting}>Refresh documents</button></div>
    <p className="small muted">Indexed documents are available to questions. Incomplete operations may have partial data.</p>
    {loading && <p role="status">Loading documents…</p>}
    {error && <p className="notice error" role="alert">{error}</p>}
    {notice && <p className="notice success" role="status">{notice}</p>}
    {!loading && !error && documents.length === 0 && <p className="muted">No documents yet.</p>}
    <ul aria-label="Documents">{documents.map(document => <li key={document.document_id}>
      <div><strong className="filename">{document.filename}</strong>
        <p className="small">{stateLabels[document.state]} · {document.page_count === null ? 'Page count unavailable' : `${document.page_count} pages`} · {document.chunk_count} chunks</p>
        {document.original_retained_on_delete && <p className="small muted">Legacy original will be retained on deletion.</p>}</div>
      <button className="button danger" disabled={deleting} aria-label={`Delete ${document.filename}`}
        onClick={() => { setSelected(document); setError(''); setNotice(''); }}>Delete</button>
    </li>)}</ul>
    {selected && <div className="delete-confirm" role="group" aria-labelledby="delete-heading">
      <h3 id="delete-heading">Delete {selected.filename}?</h3>
      <p className="small">All indexed chunks for this document will be removed.
        {selected.original_retained_on_delete ? ' Its legacy original PDF will remain in storage.' : ' Its original PDF will also be deleted.'}
        {' '}Existing answers are not updated. This cannot be undone in the app.</p>
      <div className="confirm-actions">
        <button ref={cancel} className="button secondary" disabled={deleting} onClick={() => { setSelected(null); refreshButton.current?.focus(); }}>Cancel</button>
        <button className="button danger" disabled={deleting} onClick={() => void remove()}>{deleting ? 'Deleting…' : 'Confirm deletion'}</button>
      </div>
    </div>}
  </section>;
}
