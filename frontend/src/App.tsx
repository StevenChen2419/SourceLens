import { AnswerWorkspace } from './components/AnswerWorkspace';
import { UploadPanel } from './components/UploadPanel';

export default function App() {
  return <>
    <header className="header"><a className="brand" href="#main" aria-label="KnowledgeOps home"><span className="brand-mark" aria-hidden="true">K<span>↗</span></span>KnowledgeOps</a><span className="header-label">DOCUMENT WORKSPACE</span></header>
    <main id="main">
      <div className="intro"><div className="eyebrow"><span />KNOWLEDGE, WITH CONTEXT</div>
        <h1>Answers you can<br /><span>trace to the source.</span></h1>
        <p>Ask questions grounded in your documents.<br />Less searching. More understanding.</p>
      </div>
      <div className="layout"><UploadPanel /><AnswerWorkspace /></div>
      <footer>KnowledgeOps <span>Built around your evidence. Verify important details in the source.</span></footer>
    </main>
  </>;
}
