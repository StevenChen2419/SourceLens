import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import App from './App';

const questions = [
  'How many vacation days do full-time employees receive per calendar year?',
  'How many days per week may employees work remotely, and whose approval is required?',
  'What is the capital of Japan?',
];
const config = { mode: 'public_demo', can_manage_documents: false, max_question_chars: 500,
  demo_filename: 'employee-handbook.pdf', suggested_questions: questions };
function response(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}
function mockAnswer(body: unknown, status = 200) {
  const fetch = vi.fn().mockImplementation((url: string) => Promise.resolve(
    url.endsWith('/api/config') ? response(config) : response(body, status)));
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

it('gets demo capabilities from backend, hides management and submits a suggested question', async () => {
  const fetch = mockAnswer({ question: questions[0], status: 'supported', answer: '15 paid days.',
    citations: [{ filename: 'employee-handbook.pdf', page_number: 1 }] });
  render(<App />);
  expect(screen.queryByLabelText('Choose PDF')).not.toBeInTheDocument();
  expect(await screen.findByText('Public Demo')).toBeInTheDocument();
  expect(screen.getByText('Synthetic employee handbook')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Refresh documents' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Delete/ })).not.toBeInTheDocument();
  const user = userEvent.setup();
  for (const question of questions) {
    await user.click(screen.getByRole('button', { name: question }));
    expect(screen.getByLabelText('Your question')).toHaveValue(question);
  }
  await user.click(screen.getByRole('button', { name: questions[0] }));
  await user.click(screen.getByRole('button', { name: /Ask question/ }));
  expect(await screen.findByText('15 paid days.')).toBeInTheDocument();
  expect(screen.getByText('· Page 1')).toBeInTheDocument();
  expect(screen.getByLabelText('Your question')).toHaveAttribute('maxlength', '500');
  expect(fetch.mock.calls.map(call => call[0])).toEqual([
    expect.stringMatching(/\/api\/config$/), expect.stringMatching(/\/api\/answers$/)]);
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ question: questions[0] });
});

it('shows abstention without sources', async () => {
  mockAnswer({ question: questions[2], status: 'insufficient_evidence',
    answer: 'The retrieved documents do not contain enough evidence to answer this question.', citations: [] });
  render(<App />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: questions[2] }));
  await user.click(screen.getByRole('button', { name: /Ask question/ }));
  expect(await screen.findByText('Not enough evidence')).toBeInTheDocument();
  expect(screen.queryByText('Sources')).not.toBeInTheDocument();
});

it.each([429, 503, 504])('shows friendly errors for status %s', async status => {
  mockAnswer({ detail: 'private Azure error' }, status);
  render(<App />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: questions[0] }));
  await user.click(screen.getByRole('button', { name: /Ask question/ }));
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent(status === 429 ? /limit|busy/ : status === 504 ? /timed out/ : /answer/);
  expect(screen.queryByText(/private Azure/)).not.toBeInTheDocument();
});

it.each([null, { ...config, mode: 'authenticated' }, { ...config, can_manage_documents: true }])('fails closed on invalid capabilities', async invalid => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(invalid)));
  render(<App />);
  expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable');
  expect(screen.queryByLabelText('Choose PDF')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Ask question/ })).not.toBeInTheDocument();
});

it('fails closed when backend config is unavailable', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network')));
  render(<App />);
  expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable');
  expect(screen.queryByLabelText('Your question')).not.toBeInTheDocument();
});
