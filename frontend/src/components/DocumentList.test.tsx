import { act, render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { DocumentList } from './DocumentList';
import App from '../App';

const document = {
  document_id: 'internal-document-id', filename: 'lifecycle.pdf', page_count: 2, chunk_count: 3,
  state: 'indexed', original_retained_on_delete: false,
};
function response(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

it('loads documents and refreshes states without displaying internal IDs', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(response({ documents: [document] }))
    .mockResolvedValueOnce(response({ documents: [{ ...document, state: 'failed', page_count: null }] }));
  vi.stubGlobal('fetch', fetch);
  render(<DocumentList revision={0} />);
  expect(screen.getByText('Loading documents…')).toBeInTheDocument();
  expect(await screen.findByText('lifecycle.pdf')).toBeInTheDocument();
  expect(screen.getByText(/Indexed · 2 pages · 3 chunks/)).toBeInTheDocument();
  expect(screen.queryByText('internal-document-id')).not.toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole('button', { name: 'Refresh documents' }));
  expect(await screen.findByText(/Ingestion incomplete · Page count unavailable/)).toBeInTheDocument();
  expect(fetch.mock.calls[0][1].method).toBe('GET');
});

it('shows list failure and recovers on refresh', async () => {
  const fetch = vi.fn().mockRejectedValueOnce(new TypeError('Network'))
    .mockResolvedValueOnce(response({ documents: [] }));
  vi.stubGlobal('fetch', fetch);
  render(<DocumentList revision={0} />);
  expect(await screen.findByRole('alert')).toHaveTextContent(/backend is running/);
  expect(screen.queryByText('No documents yet.')).not.toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole('button', { name: 'Refresh documents' }));
  expect(await screen.findByText('No documents yet.')).toBeInTheDocument();
});

it('requires explicit confirmation; cancel sends no DELETE', async () => {
  const fetch = vi.fn().mockResolvedValue(response({ documents: [document] }));
  vi.stubGlobal('fetch', fetch);
  render(<DocumentList revision={0} />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: 'Delete lifecycle.pdf' }));
  expect(screen.getByRole('group', { name: 'Delete lifecycle.pdf?' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();
  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.queryByRole('button', { name: 'Confirm deletion' })).not.toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('waits for confirmed deletion before removing the document', async () => {
  let complete!: (value: Response) => void;
  const pending = new Promise<Response>(resolve => { complete = resolve; });
  const fetch = vi.fn().mockImplementation((_url, options) => options.method === 'GET'
    ? Promise.resolve(response({ documents: [document] })) : pending);
  vi.stubGlobal('fetch', fetch);
  render(<DocumentList revision={0} />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: 'Delete lifecycle.pdf' }));
  await user.click(screen.getByRole('button', { name: 'Confirm deletion' }));
  expect(screen.getByRole('button', { name: 'Deleting…' })).toBeDisabled();
  expect(within(screen.getByRole('list', { name: 'Documents' })).getByText('lifecycle.pdf')).toBeInTheDocument();
  const [url, options] = fetch.mock.calls[1];
  expect(url).toMatch(/\/api\/documents\/internal-document-id$/);
  expect(options.method).toBe('DELETE');
  await act(async () => complete(response({ document_id: document.document_id, status: 'deleted', original_retained: false })));
  expect(await screen.findByText('Document and original PDF deleted.')).toBeInTheDocument();
  expect(screen.queryByText('lifecycle.pdf')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Refresh documents' })).toHaveFocus();
});

it('shows partial deletion, refreshes state, and allows retry', async () => {
  const fetch = vi.fn()
    .mockResolvedValueOnce(response({ documents: [document] }))
    .mockResolvedValueOnce(response({ detail: 'private SDK information' }, 503))
    .mockResolvedValueOnce(response({ documents: [{ ...document, state: 'deleting' }] }))
    .mockResolvedValueOnce(response({ document_id: document.document_id, status: 'deleted', original_retained: false }));
  vi.stubGlobal('fetch', fetch);
  render(<DocumentList revision={0} />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: 'Delete lifecycle.pdf' }));
  await user.click(screen.getByRole('button', { name: 'Confirm deletion' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Deletion is incomplete');
  expect(screen.getByText(/Deletion incomplete · 2 pages/)).toBeInTheDocument();
  expect(screen.queryByText(/private SDK/)).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Confirm deletion' }));
  expect(await screen.findByText('Document and original PDF deleted.')).toBeInTheDocument();
});

it('clearly distinguishes legacy original retention', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(response({ documents: [{ ...document, original_retained_on_delete: true }] }))
    .mockResolvedValueOnce(response({ document_id: document.document_id, status: 'deleted', original_retained: true }));
  vi.stubGlobal('fetch', fetch);
  render(<DocumentList revision={0} />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: 'Delete lifecycle.pdf' }));
  expect(screen.getByText(/Its legacy original PDF will remain/)).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Confirm deletion' }));
  expect(await screen.findByText('Document removed from Search. No legacy original PDF was deleted.')).toBeInTheDocument();
});

it.each(['indexed', 'failed'])('explains duplicate upload with state %s and refreshes documents', async state => {
  const fetch = vi.fn().mockImplementation((_url, options) => Promise.resolve(options.method === 'GET'
    ? response({ documents: [{ ...document, state }] })
    : response({ detail: { code: 'duplicate_document', state, document_id: document.document_id } }, 409)));
  vi.stubGlobal('fetch', fetch);
  render(<App />);
  const user = userEvent.setup();
  await screen.findByText('lifecycle.pdf');
  await user.upload(screen.getByLabelText('Choose PDF'), new File(['%PDF'], 'renamed.pdf', { type: 'application/pdf' }));
  await user.click(screen.getByRole('button', { name: /Upload document/ }));
  expect(await screen.findByRole('alert')).toHaveTextContent(state === 'indexed' ? 'already indexed' : 'incomplete document operation');
  expect(screen.queryByText('Document indexed')).not.toBeInTheDocument();
  await waitFor(() => expect(fetch.mock.calls.filter(call => call[1].method === 'GET')).toHaveLength(2));
  expect(screen.queryByText(document.document_id)).not.toBeInTheDocument();
});

it('refreshes the document list after a successful upload', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(response({ documents: [] }))
    .mockResolvedValueOnce(response({ ...document }, 201))
    .mockResolvedValueOnce(response({ documents: [document] }));
  vi.stubGlobal('fetch', fetch);
  render(<App />);
  await screen.findByText('No documents yet.');
  const user = userEvent.setup();
  await user.upload(screen.getByLabelText('Choose PDF'), new File(['%PDF'], document.filename, { type: 'application/pdf' }));
  await user.click(screen.getByRole('button', { name: /Upload document/ }));
  expect(await screen.findByRole('button', { name: 'Delete lifecycle.pdf' })).toBeInTheDocument();
  expect(screen.getByText('Document indexed')).toBeInTheDocument();
});

it('a stale list response cannot restore a document removed by deletion', async () => {
  let finishRefresh!: (value: Response) => void;
  const fetch = vi.fn().mockResolvedValueOnce(response({ documents: [document] }))
    .mockReturnValueOnce(new Promise<Response>(resolve => { finishRefresh = resolve; }))
    .mockResolvedValueOnce(response({ status: 'deleted', original_retained: false }));
  vi.stubGlobal('fetch', fetch);
  render(<DocumentList revision={0} />);
  const user = userEvent.setup();
  await screen.findByText('lifecycle.pdf');
  await user.click(screen.getByRole('button', { name: 'Refresh documents' }));
  await user.click(screen.getByRole('button', { name: 'Delete lifecycle.pdf' }));
  await user.click(screen.getByRole('button', { name: 'Confirm deletion' }));
  await screen.findByText('Document and original PDF deleted.');
  await act(async () => finishRefresh(response({ documents: [document] })));
  expect(screen.queryByText('lifecycle.pdf')).not.toBeInTheDocument();
});
