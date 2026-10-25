import { useEffect, useRef, useState, type DragEvent, type KeyboardEvent } from 'react';
import { ApiError } from '../../api';
import AppHeader from '../../components/AppHeader';
import {
  ACCEPTED, formatBytes, isAccepted, listSources, plural, rowsIn, statusText, summarize, testSource, uploadLogs,
  type Acquisition, type Source, type SourceStatus,
} from './ingestion';
import './ingestion.css';

const errorText = (e: unknown) =>
  e instanceof ApiError ? e.message : 'Something went wrong. Try again.';

function SourceCard({ source }: { source: Source }) {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<SourceStatus | null>(null);

  async function test() {
    setTesting(true);
    try {
      setResult(await testSource(source.id));
    } catch (e) {
      setResult({ ok: false, reason: errorText(e) });
    } finally {
      setTesting(false);
    }
  }

  return (
    <li className={`ingest-source ${source.configured ? 'ready' : 'off'}`}>
      <div className="ingest-source-top">
        <strong>{source.display_name}</strong>
        <span className="ingest-badge">{source.configured ? '✓ Ready' : '○ Not set up'}</span>
      </div>
      <p className="muted">{source.needs_network ? 'Needs a network connection' : 'Works offline'}</p>
      {!source.configured && source.reason && <p className="ingest-source-reason">{source.reason}</p>}
      <button className="btn ghost" onClick={test} disabled={testing}>
        {testing ? 'Testing…' : 'Test connection'}
      </button>
      {result && (
        <p className={`ingest-test ${result.ok ? 'ok' : 'bad'}`} role="status">
          {result.ok ? `✓ ${result.reason || 'Connected'}` : `✗ Can't connect: ${result.reason}`}
        </p>
      )}
    </li>
  );
}

export default function IngestionPage() {
  const [sources, setSources] = useState<Source[] | null>(null);
  const [sourcesError, setSourcesError] = useState<string | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [importing, setImporting] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState<Acquisition | null>(null);
  const [error, setError] = useState<string | null>(null);
  const picker = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listSources().then(setSources).catch((e) => setSourcesError(errorText(e)));
  }, []);

  useEffect(() => {
    if (!importing) return;
    const start = Date.now();
    setElapsed(0);
    const t = window.setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 500);
    return () => window.clearInterval(t);
  }, [importing]);

  function choose(list: FileList | null) {
    if (!list) return;
    const chosen = Array.from(list);
    const bad = chosen.filter((f) => !isAccepted(f.name));
    setError(bad.length ? `${bad.map((f) => f.name).join(', ')}: not a supported log file (${ACCEPTED.join(', ')}).` : null);
    setFiles(chosen.filter((f) => isAccepted(f.name)));
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    choose(e.dataTransfer.files);
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      picker.current?.click();
    }
  }

  async function runImport() {
    setImporting(true);
    setError(null);
    try {
      setResult(await uploadLogs(files));
      setFiles([]);
      if (picker.current) picker.current.value = '';
    } catch (e) {
      setError(errorText(e));
    } finally {
      setImporting(false);
    }
  }

  const summary = result && summarize(result);
  const clock = `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, '0')}`;

  return (
    <>
      <AppHeader />
      <main className="ingest">
        <h2>Log import</h2>
        <p className="muted ingest-intro">
          Bring logs into Portrait IQ from wherever they are. Only new files are imported; anything already imported is
          skipped.
        </p>

        <section aria-labelledby="ingest-sources">
          <h3 id="ingest-sources">1. Where logs can come from</h3>
          {sourcesError && <div className="ingest-banner bad" role="alert">{sourcesError}</div>}
          {!sources && !sourcesError && <p className="muted">Loading sources…</p>}
          {sources && (
            <ul className="ingest-sources">
              {sources.map((s) => <SourceCard key={s.id} source={s} />)}
            </ul>
          )}
        </section>

        <section aria-labelledby="ingest-import">
          <h3 id="ingest-import">2. Import a log file or ZIP</h3>
          <div
            className={`ingest-drop ${dragging ? 'over' : ''}`}
            role="button"
            tabIndex={0}
            aria-describedby="ingest-drop-help"
            onClick={() => picker.current?.click()}
            onKeyDown={onKey}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            <strong>Drop log files or a ZIP here</strong>
            <span id="ingest-drop-help" className="muted">or click to choose ({ACCEPTED.join(', ')})</span>
          </div>
          <input
            ref={picker}
            type="file"
            multiple
            accept={ACCEPTED.join(',')}
            className="ingest-picker"
            tabIndex={-1}
            aria-hidden="true"
            onChange={(e) => choose(e.target.files)}
          />
          {files.length > 0 && (
            <ul className="ingest-chosen">
              {files.map((f) => <li key={f.name + f.size}>{f.name} <span className="muted">({formatBytes(f.size)})</span></li>)}
            </ul>
          )}
          <div className="ingest-actions">
            <button className="btn" onClick={runImport} disabled={importing || files.length === 0}>
              {importing ? 'Importing…' : files.length > 1 ? `Import ${files.length} files` : 'Import'}
            </button>
            {importing && (
              <span className="muted" role="status" aria-live="polite">
                Importing… {clock}. Large ZIPs can take a few seconds.
              </span>
            )}
          </div>
          {error && <div className="ingest-banner bad" role="alert">{error}</div>}
        </section>

        <section aria-labelledby="ingest-result">
          <h3 id="ingest-result">3. What happened</h3>
          {!result && <p className="muted">Import some logs to see the result here.</p>}
          {result && summary && (
            <>
              <div className={`ingest-banner ${summary.tone}`} role="status" aria-live="polite">{summary.headline}</div>
              <div className="ingest-counts">
                <div><span className="ingest-count">{result.new_files.toLocaleString()}</span>New files imported</div>
                <div><span className="ingest-count">{result.already_acquired.toLocaleString()}</span>Already imported (skipped)</div>
                <div><span className="ingest-count">{result.failed.toLocaleString()}</span>Failed</div>
                <div><span className="ingest-count">{rowsIn(result).toLocaleString()}</span>Log rows added</div>
              </div>
              <ul className="ingest-files">
                {result.files.map((f) => {
                  const s = statusText(f);
                  return (
                    <li key={f.name + f.status} className={`ingest-file ${s.tone}`}>
                      <div className="ingest-file-row">
                        <span>{f.name}</span>
                        <span className="muted">{formatBytes(f.size)}</span>
                      </div>
                      <p className="ingest-file-status">{s.text}</p>
                    </li>
                  );
                })}
              </ul>
              {result.notes?.map((n) => <p key={n} className="ingest-note">{n}</p>)}
              <p className="muted ingest-foot">
                {plural(result.files.length, 'file')} checked. Only counts and file names are shown here, never log
                contents.
              </p>
            </>
          )}
        </section>
      </main>
    </>
  );
}
