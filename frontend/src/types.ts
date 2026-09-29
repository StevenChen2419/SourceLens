export interface DocumentUploadResponse {
  document_id: string;
  filename: string;
  page_count: number;
  chunk_count: number;
}

export interface Citation {
  source_id: string;
  document_id: string;
  filename: string;
  page_number: number;
  chunk_id: string;
  chunk_index: number;
}

export interface AnswerResponse {
  question: string;
  status: 'supported' | 'insufficient_evidence';
  answer: string;
  citations: Citation[];
}
