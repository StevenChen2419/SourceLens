import { useRef, useState } from 'react';
import { uploadDocument } from '../api';
import type { DocumentUploadResponse } from '../types';

export function UploadPanel() {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState('');
  const [uploaded, setUploaded] = useState<DocumentUploadResponse | null>(null);

  function select(files: FileList | null) {
    if (busy) return;
    setError('');
    setUploaded(null);
    setFile(null);
    if (!files?.length) return;
    if (files.length !== 1 || !files[0].name.toLowerCase().endsWith('.pdf')) {
      setError('Choose one PDF file at a time.');
      return;
    }
    setFile(files[0]);
  }

  async function upload() {
    if (!file || busy) return;
    setBusy(true);
    setError('');
    setUploaded(null);
    try {
      setUploaded(await uploadDocument(file));
      setFile(null);
      if (input.current) input.current.value = '';
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Upload failed. Please try again.');
    } finally {
      setBusy(false);
    }
  }

  return <section className="card knowledge" aria-labelledby="knowledge-heading">
    <div className="section-number">01 / YOUR SOURCES</div>
    <h2 id="knowledge-heading">Knowledge Base</h2>
    <p className="muted">Add the documents your answers will draw from.</p>
    <div className={`drop-zone ${dragging ? 'dragging' : ''}`}
      onDragOver={event => { event.preventDefault(); if (!busy) setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={event => { event.preventDefault(); setDragging(false); select(event.dataTransfer.files); }}>
      <div className="file-symbol" aria-hidden="true">PDF<span>↑</span></div>
      <strong>{file ? file.name : 'Drop a PDF here'}</strong>
      <span className="muted small">{file ? `${(file.size / 1024).toFixed(0)} KB · Ready to upload` : 'or choose a file from your device'}</span>
      <label className={`file-picker ${busy ? 'disabled' : ''}`}>
        {file ? 'Choose another PDF' : 'Choose PDF'}
        <input ref={input} aria-label="Choose PDF" type="file" accept=".pdf,application/pdf"
          disabled={busy} onChange={event => select(event.target.files)} />
      </label>
    </div>
    <p className="small muted format-note">Text-based PDFs only. Scanned and encrypted documents aren’t supported.</p>
    <button className="button primary full" disabled={!file || busy} onClick={upload}>
      {busy ? 'Uploading and indexing…' : 'Upload document'}<span aria-hidden="true">↑</span>
    </button>
    <div aria-live="polite" aria-atomic="true">
      {busy && <div className="notice loading" role="status"><span className="spinner" aria-hidden="true" />Preparing your document. This may take a moment.</div>}
      {uploaded && <div className="notice success" role="status">
        <strong>Document indexed</strong><span className="filename">{uploaded.filename}</span>
        <span>{uploaded.page_count} {uploaded.page_count === 1 ? 'page' : 'pages'} · {uploaded.chunk_count} {uploaded.chunk_count === 1 ? 'chunk' : 'chunks'}</span>
      </div>}
    </div>
    {error && <p className="notice error" role="alert">{error}</p>}
    <div className="source-note"><span aria-hidden="true">↳</span><p>Already uploaded a document?<br />You can ask about it right away.</p></div>
  </section>;
}
