import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import App from './App';

const citation = {
  source_id: 'internal-source', document_id: 'internal-document', filename: 'handbook.pdf',
  page_number: 1, chunk_id: 'internal-chunk', chunk_index: 0,
};
const supported = {
  question: 'How much vacation?', status: 'supported', answer: 'Employees receive 15 days.',
  citations: [citation],
};
function response(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}
function mockResponse(body: unknown, status = 200) {
  const fetch = vi.fn().mockResolvedValue(response(body, status));
  vi.stubGlobal('fetch', fetch);
  return fetch;
}
async function ask() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText('Your question'), '  How much vacation?  ');
  await user.click(screen.getByRole('button', { name: /Ask question/ }));
}
async function selectPdf() {
  const file = new File(['%PDF-test'], 'handbook.pdf', { type: 'application/pdf' });
  await userEvent.setup().upload(screen.getByLabelText('Choose PDF'), file);
  return file;
}

describe('document upload', () => {
  it('sends one multipart PDF and waits for indexing before showing success', async () => {
    let complete!: (value: Response) => void;
    const fetch = vi.fn().mockReturnValue(new Promise<Response>(resolve => { complete = resolve; }));
    vi.stubGlobal('fetch', fetch);
    render(<App />);
    const file = await selectPdf();
    await userEvent.setup().click(screen.getByRole('button', { name: /Upload document/ }));
    expect(screen.getByRole('button', { name: /Uploading and indexing/ })).toBeDisabled();
    expect(screen.queryByText('Document indexed')).not.toBeInTheDocument();
    const [url, options] = fetch.mock.calls[0];
    expect(url).toMatch(/\/api\/documents$/);
    expect(options.method).toBe('POST');
    expect(options.body.getAll('file')).toEqual([file]);
    expect(options.headers).toBeUndefined(); // Browser sets the multipart boundary.
    await act(async () => complete(response({ document_id: 'private-id', filename: 'handbook.pdf', page_count: 3, chunk_count: 7 }, 201)));
    expect(await screen.findByText('Document indexed')).toBeInTheDocument();
    expect(screen.getByText('3 pages · 7 chunks')).toBeInTheDocument();
    expect(screen.getByText('handbook.pdf')).toBeInTheDocument();
    expect(screen.queryByText('private-id')).not.toBeInTheDocument();
  });

  it('shows invalid PDF errors without claiming success', async () => {
    mockResponse({ detail: 'unsupported' }, 422);
    render(<App />);
    await selectPdf();
    await userEvent.setup().click(screen.getByRole('button', { name: /Upload document/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/PDF/);
    expect(screen.queryByText('Document indexed')).not.toBeInTheDocument();
  });

  it('reports upload failure without exposing internal backend details', async () => {
    mockResponse({ detail: { document_id: 'secret-internal-id' } }, 503);
    render(<App />);
    await selectPdf();
    await userEvent.setup().click(screen.getByRole('button', { name: /Upload document/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/may have been saved/);
    expect(screen.queryByText(/secret-internal-id/)).not.toBeInTheDocument();
  });

  it('accepts a dropped PDF but rejects multiple files and non-PDF files locally', () => {
    const fetch = mockResponse({});
    render(<App />);
    const zone = screen.getByText('Drop a PDF here').closest('.drop-zone')!;
    const pdf = new File(['pdf'], 'dropped.pdf', { type: 'application/pdf' });
    fireEvent.drop(zone, { dataTransfer: { files: [pdf] } });
    expect(screen.getByText('dropped.pdf')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Upload document/ })).toBeEnabled();
    for (const files of [[pdf, pdf], [new File(['text'], 'notes.txt')]]) {
      fireEvent.drop(zone, { dataTransfer: { files } });
      expect(screen.getByRole('alert')).toHaveTextContent('Choose one PDF');
      expect(screen.getByRole('button', { name: /Upload document/ })).toBeDisabled();
    }
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe('grounded answers', () => {
  it('renders a supported answer and deduplicates trusted page citations', async () => {
    const fetch = mockResponse({ ...supported, citations: [citation, { ...citation, chunk_id: 'another' }, { ...citation, page_number: 2 }] });
    render(<App />);
    await ask();
    expect(await screen.findByText(supported.answer)).toBeInTheDocument();
    expect(screen.getByText('Based on your documents')).toBeInTheDocument();
    const sources = screen.getByRole('list');
    expect(within(sources).getAllByRole('listitem')).toHaveLength(2);
    expect(sources).toHaveTextContent('handbook.pdf · Page 1');
    expect(sources).toHaveTextContent('handbook.pdf · Page 2');
    expect(screen.queryByText(/internal-/)).not.toBeInTheDocument();
    expect(fetch.mock.calls[0][0]).toMatch(/\/api\/answers$/);
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ question: 'How much vacation?' });
  });

  it('shows the insufficient-evidence response without sources', async () => {
    mockResponse({ ...supported, status: 'insufficient_evidence', answer: 'The supplied documents do not answer this question.', citations: [] });
    render(<App />);
    await ask();
    expect(await screen.findByText('Not enough evidence')).toBeInTheDocument();
    expect(screen.getByText('The supplied documents do not answer this question.')).toBeInTheDocument();
    expect(screen.queryByText('Sources')).not.toBeInTheDocument();
    expect(screen.queryByRole('list')).not.toBeInTheDocument();
  });

  it('disables empty questions and duplicate submissions while waiting', async () => {
    let complete!: (value: Response) => void;
    const fetch = vi.fn().mockReturnValue(new Promise<Response>(resolve => { complete = resolve; }));
    vi.stubGlobal('fetch', fetch);
    render(<App />);
    expect(screen.getByRole('button', { name: /Ask question/ })).toBeDisabled();
    await ask();
    expect(screen.getByText('Looking through your documents')).toBeInTheDocument();
    expect(screen.getByLabelText('Your question')).toBeDisabled();
    await userEvent.setup().click(screen.getByRole('button', { name: /Finding your answer/ }));
    expect(fetch).toHaveBeenCalledTimes(1);
    await act(async () => complete(response(supported)));
    expect(await screen.findByText(supported.answer)).toBeInTheDocument();
  });

  it.each([502, 503])('reports generation/API failure (%s)', async status => {
    mockResponse({ detail: 'Azure internal details' }, status);
    render(<App />);
    await ask();
    expect(await screen.findByRole('alert')).toHaveTextContent(/answer/i);
    expect(screen.queryByText('Sources')).not.toBeInTheDocument();
    expect(screen.queryByText(/Azure internal details/)).not.toBeInTheDocument();
  });

  it('explains network/backend unavailability', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    render(<App />);
    await ask();
    expect(await screen.findByRole('alert')).toHaveTextContent(/backend is running/);
  });
});
