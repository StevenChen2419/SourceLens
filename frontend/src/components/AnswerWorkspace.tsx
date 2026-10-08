import { useState, type FormEvent } from 'react';
import { askQuestion } from '../api';
import type { AnswerResponse } from '../types';

export function AnswerWorkspace({ maxQuestionChars = 4000, suggestedQuestions = [] }: { maxQuestionChars?: number; suggestedQuestions?: string[] }) {
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [answer, setAnswer] = useState<AnswerResponse | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!question.trim() || busy) return;
    setBusy(true); setError(''); setAnswer(null);
    try {
      setAnswer(await askQuestion(question.trim()));
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Unable to generate an answer.');
    } finally { setBusy(false); }
  }

  // Multiple chunks can cite the same page; display a single page reference.
  const citations = answer?.citations.filter((citation, index, all) =>
    all.findIndex(item => (item.document_id ?? item.filename) === (citation.document_id ?? citation.filename) && item.page_number === citation.page_number) === index,
  ) ?? [];

  return <section className="workspace" aria-labelledby="ask-heading">
    <div className="card question-card">
      <div className="section-number">02 / FIND YOUR ANSWER</div>
      <h2 id="ask-heading">Ask Your Documents</h2>
      <p className="muted">Ask naturally. Get an answer with the evidence behind it.</p>
      {suggestedQuestions.length > 0 && <div className="suggested-questions" aria-label="Suggested questions">
        <p className="small muted">Try an example</p>{suggestedQuestions.map(example =>
          <button key={example} type="button" className="button" disabled={busy} onClick={() => setQuestion(example)}>{example}</button>)}
      </div>}
      <form onSubmit={submit}>
        <label className="field-label" htmlFor="question">Your question</label>
        <textarea id="question" value={question} maxLength={maxQuestionChars} disabled={busy}
          onChange={event => setQuestion(event.target.value)} rows={4}
          placeholder="What does our policy say about working from home?" aria-describedby="question-help" />
        <div className="question-actions">
          <span id="question-help" className="small muted">One question at a time. Up to {maxQuestionChars.toLocaleString()} characters.</span>
          <button className="button primary" disabled={busy || !question.trim()} type="submit">
            {busy ? 'Finding your answer…' : 'Ask question'}<span aria-hidden="true">↗</span>
          </button>
        </div>
      </form>
      {error && <p className="notice error" role="alert">{error}</p>}
    </div>
    <div aria-live="polite" aria-atomic="true" aria-busy={busy}>
      {busy && <div className="card answer-placeholder" role="status"><span className="spinner large" aria-hidden="true" /><h3>Looking through your documents</h3><p className="muted">Finding relevant passages and preparing a grounded answer.</p></div>}
      {!busy && !answer && !error && <div className="card answer-placeholder">
        <div className="evidence-symbol" aria-hidden="true">≋</div>
        <h3>Your documents. A clearer answer.</h3>
        <p className="muted">Ask a question to see an answer here.<br />Source references make it easy to check the details.</p>
        <div className="evidence-caption">EVIDENCE FIRST · SOURCES INCLUDED</div>
      </div>}
      {answer && <article className={`card answer-card ${answer.status === 'insufficient_evidence' ? 'insufficient' : ''}`}>
        <div className="answer-top"><h3>{answer.status === 'supported' ? 'Your answer' : 'Not enough evidence'}</h3>
          <span className="badge">{answer.status === 'supported' ? 'Based on your documents' : 'Insufficient evidence'}</span></div>
        <p className="answered-question">{answer.question}</p>
        <p className="answer-text">{answer.answer}</p>
        {answer.status === 'supported' && citations.length > 0 && <div className="sources">
          <h4>Sources</h4>
          <ul>{citations.map(citation => <li key={`${citation.document_id ?? citation.filename}:${citation.page_number}`}>
            <span aria-hidden="true" className="source-icon">↗</span><span>{citation.filename} <span className="page">· Page {citation.page_number}</span></span>
          </li>)}</ul>
          <p className="small muted">Check these pages in your original PDF.</p>
        </div>}
      </article>}
    </div>
  </section>;
}
