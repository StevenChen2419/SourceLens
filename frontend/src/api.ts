import type { AnswerResponse, DocumentUploadResponse } from './types';

const baseUrl = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

async function request<T>(path: string, options: RequestInit, kind: 'upload' | 'answer'): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, options);
  } catch {
    throw new Error('Unable to reach KnowledgeOps. Check your connection and that the backend is running.');
  }
  if (!response.ok) {
    if (kind === 'upload') {
      if (response.status === 413) throw new Error('This PDF exceeds the upload size limit. Try a smaller file.');
      if (response.status === 415) throw new Error('Choose a valid PDF file. Other file types are not supported.');
      if (response.status === 422) throw new Error('This PDF could not be read. Use a text-based, unencrypted PDF; scanned documents are not supported.');
      throw new Error('Upload could not be completed. The PDF or some of its content may have been saved. Check the backend before retrying.');
    }
    if (response.status === 422) throw new Error('Enter a question of up to 4,000 characters.');
    throw new Error('We couldn’t generate an answer. Please try again. If this continues, check the backend services.');
  }
  try {
    return await response.json() as T;
  } catch {
    throw new Error('The backend returned an unreadable response. Please try again.');
  }
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
