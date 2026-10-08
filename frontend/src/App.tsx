import { AnswerWorkspace } from './components/AnswerWorkspace';
import { UploadPanel } from './components/UploadPanel';
import { DocumentList } from './components/DocumentList';
import { useEffect, useState } from 'react';
import { getPublicConfiguration } from './api';
import type { PublicConfiguration } from './types';

export default function App() {
  const [config, setConfig] = useState<PublicConfiguration | null>(null);
  const [configError, setConfigError] = useState('');
  useEffect(() => {
    let current = true;
    getPublicConfiguration().then(value => { if (current) setConfig(value); })
      .catch(() => { if (current) setConfigError('SourceLens is temporarily unavailable. Please reload to try again.'); });
    return () => { current = false; };
  }, []);
  const [documentsRevision, setDocumentsRevision] = useState(0);
  return <>
    <header className="header"><a className="brand" href="#main" aria-label="SourceLens home"><span className="brand-mark" aria-hidden="true">S<span>↗</span></span>SourceLens</a><span className="header-label">{config?.mode === 'public_demo' ? 'Public Demo' : 'DOCUMENT WORKSPACE'}</span></header>
    <main id="main">
      <div className="intro"><div className="eyebrow"><span />YOUR DOCUMENTS, IN FOCUS</div>
        <h1>Answers you can<br /><span>trace to the source.</span></h1>
        <p>Ask questions grounded in your documents.<br />Less searching. More understanding.</p>
      </div>
      {!config && <p role={configError ? 'alert' : 'status'}>{configError || 'Loading SourceLens…'}</p>}
      {config && <div className="layout"><div className="workspace">
        {config.can_manage_documents ? <><UploadPanel onDocumentsChanged={() => setDocumentsRevision(value => value + 1)} /><DocumentList revision={documentsRevision} /></>
          : <section className="card"><div className="section-number">01 / YOUR SOURCE</div><h2>Synthetic employee handbook</h2>
              <p className="muted">Explore real AI answers grounded in a six-page synthetic handbook. Uploads and document management are unavailable in this public demo.</p>
              <p>{config.demo_filename}</p><p className="small muted">AI answers can be wrong. Inspect the page citations and avoid entering personal information.</p></section>}
      </div><AnswerWorkspace maxQuestionChars={config.max_question_chars} suggestedQuestions={config.suggested_questions} /></div>}
      <footer>SourceLens <span>Built around your evidence. Verify important details in the source.</span></footer>
    </main>
  </>;
}
