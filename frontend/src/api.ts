import type { AnswerResponse, DocumentUploadResponse, DocumentListResponse, DocumentDeleteResponse, PublicConfiguration } from './types';

// Vite rejects production builds without an HTTPS API origin. Local fallback is dev-only.
const baseUrl = (import.meta.env.VITE_API_BASE_URL || (import.meta.env.DEV ? 'http://127.0.0.1:8000' : '')).replace(/\/$/, '');

async function request<T>(path: string, options: RequestInit, kind: 'upload' | 'answer' | 'list' | 'delete' | 'config'): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, options);
  } catch {
    throw new Error('Unable to reach SourceLens. Check your connection and try again later.');
  }
  if (!response.ok) {
    if (response.status === 429) throw new Error('The demo is busy or its request limit has been reached. Please wait and try again.');
    if (response.status === 504) throw new Error('The answer request timed out. Please try again later.');
    if (kind === 'config') throw new Error('SourceLens configuration is unavailable. Please try again later.');
    if (response.status === 409) {
      if (kind === 'upload') {
        const body = await response.json().catch(() => null);
        if (body?.detail?.code === 'duplicate_document') {
          throw new Error(body.detail.state === 'indexed'
            ? 'This exact PDF is already indexed. No duplicate was added, even if the filename changed.'
            : 'This PDF already has an incomplete document operation. Refresh the list and delete the incomplete document before uploading again.');
        }
      }
      throw new Error('Another document operation is in progress. Please retry later.');
    }
    if (kind === 'list') throw new Error('Unable to load documents. Check the backend and Azure storage, then refresh.');
    if (kind === 'delete') throw new Error(response.status === 404
      ? 'This document was not found. Refresh the document list.'
      : 'Deletion is incomplete. Some data may remain. Refresh the list and retry deletion.');
    if (kind === 'upload') {
      if (response.status === 413) throw new Error('This PDF exceeds the upload size limit. Try a smaller file.');
      if (response.status === 415) throw new Error('Choose a valid PDF file. Other file types are not supported.');
      if (response.status === 422) throw new Error('This PDF could not be read. Use a text-based, unencrypted PDF; scanned documents are not supported.');
      throw new Error('Upload could not be completed. The PDF or some of its content may have been saved. Check the backend before retrying.');
    }
    if (response.status === 422) throw new Error('Enter a question within the displayed length limit.');
    throw new Error('We couldn’t generate an answer. Please try again later.');
  }
  try {
    return await response.json() as T;
  } catch {
    throw new Error('The backend returned an unreadable response. Please try again.');
  }
}

export function listDocuments(): Promise<DocumentListResponse> {
  return request('/api/documents', { method: 'GET' }, 'list');
}

export function deleteDocument(documentId: string): Promise<DocumentDeleteResponse> {
  return request(`/api/documents/${encodeURIComponent(documentId)}`, { method: 'DELETE' }, 'delete');
}

export function uploadDocument(file: File): Promise<DocumentUploadResponse> {
  const body = new FormData();
  body.append('file', file);
  return request('/api/documents', { method: 'POST', body }, 'upload');
}

export function askQuestion(question: string): Promise<AnswerResponse> {
  return request('/api/answers', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  }, 'answer');
}

export async function getPublicConfiguration(): Promise<PublicConfiguration> {
  const config = await request<PublicConfiguration>('/api/config', { method: 'GET' }, 'config');
  if (!['development', 'public_demo'].includes(config.mode)
      || config.can_manage_documents !== (config.mode === 'development')
      || !Number.isInteger(config.max_question_chars) || config.max_question_chars < 1 || config.max_question_chars > 4000
      || !Array.isArray(config.suggested_questions) || config.suggested_questions.some(q => typeof q !== 'string')
      || (config.mode === 'public_demo' && config.demo_filename !== 'employee-handbook.pdf')) {
    throw new Error('SourceLens returned an invalid application configuration. Please try again later.');
  }
  return config;
}
