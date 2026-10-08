export interface DocumentUploadResponse {
  document_id: string;
  filename: string;
  page_count: number;
  chunk_count: number;
}

export interface DocumentSummary extends Omit<DocumentUploadResponse, 'page_count'> {
  page_count: number | null;
  state: 'indexing' | 'indexed' | 'failed' | 'deleting';
  original_retained_on_delete: boolean;
}

export interface DocumentListResponse { documents: DocumentSummary[] }
export interface DocumentDeleteResponse {
  document_id: string;
  status: 'deleted';
  original_retained: boolean;
}

export interface Citation {
  source_id?: string;
  document_id?: string;
  filename: string;
  page_number: number;
  chunk_id?: string;
  chunk_index?: number;
}

export interface AnswerResponse {
  question: string;
  status: 'supported' | 'insufficient_evidence';
  answer: string;
  citations: Citation[];
}

export interface PublicConfiguration {
  mode: 'development' | 'public_demo';
  can_manage_documents: boolean;
  max_question_chars: number;
  demo_filename: string | null;
  suggested_questions: string[];
}
