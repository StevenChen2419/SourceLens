import { AnswerWorkspace } from './components/AnswerWorkspace';
import { UploadPanel } from './components/UploadPanel';
import { DocumentList } from './components/DocumentList';
import { useState } from 'react';

export default function App() {
  const [documentsRevision, setDocumentsRevision] = useState(0);
  return <>
    <header className="header"><a className="brand" href="#main" aria-label="SourceLens home"><span className="brand-mark" aria-hidden="true">S<span>↗</span></span>SourceLens</a><span className="header-label">DOCUMENT WORKSPACE</span></header>
    <main id="main">
      <div className="intro"><div className="eyebrow"><span />YOUR DOCUMENTS, IN FOCUS</div>
        <h1>Answers you can<br /><span>trace to the source.</span></h1>
        <p>Ask questions grounded in your documents.<br />Less searching. More understanding.</p>
      </div>
      <div className="layout"><div className="workspace"><UploadPanel onDocumentsChanged={() => setDocumentsRevision(value => value + 1)} /><DocumentList revision={documentsRevision} /></div><AnswerWorkspace /></div>
      <footer>SourceLens <span>Built around your evidence. Verify important details in the source.</span></footer>
    </main>
  </>;
}
