import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { request } from '../../services/api';
import { useApi } from '../../hooks/useApi';
import { ErrorState, LoadingSkeleton, PageHeader } from '../../components/ui';

const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const timetableRows = [
  { period_index: 0, start: '09:00', end: '09:55' },
  { break: true, start: '09:55', end: '10:15', label: 'Morning break' },
  { period_index: 1, start: '10:15', end: '11:10' },
  { period_index: 2, start: '11:10', end: '12:05' },
  { break: true, start: '12:05', end: '12:50', label: 'Lunch break' },
  { period_index: 3, start: '12:50', end: '13:50' },
  { period_index: 4, start: '13:50', end: '14:50' },
  { break: true, start: '14:50', end: '15:15', label: 'Afternoon break' },
  { period_index: 5, start: '15:15', end: '16:30' },
];
const time = (value) => `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
const breakLabels = {
  '09:55–10:15': 'Morning break', '12:05–12:50': 'Lunch break', '14:50–15:15': 'Afternoon break',
};
function configuredRows(periodDefinitions) {
  if (!periodDefinitions?.length) return timetableRows;
  const rows = new Map();
  for (const period of periodDefinitions) {
    if (period.is_closed || rows.has(period.index)) continue;
    const start = time(period.start); const end = time(period.end);
    rows.set(period.index, {
      period_index: period.index, start, end,
      break: Boolean(period.is_break || period.closed), label: breakLabels[`${start}–${end}`] || 'Break',
    });
  }
  return rows.size ? [...rows.values()].sort((a, b) => a.period_index - b.period_index) : timetableRows;
}
const message = (e) => typeof e?.message === 'string' && e.message !== '[object Object]' ? e.message : 'Timetable request failed. Review configuration and retry.';
function Notice({ text }) { return text ? <p role="status" className="my-3 rounded-lg border bg-white p-3 text-sm">{text}</p> : null; }

function useAction() {
  const gate = useRef(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const act = async (job, success = 'Saved.') => {
    if (gate.current) return;
    gate.current = true; setBusy(true); setNotice('');
    try { await job(); setNotice(success); } catch (e) { setNotice(message(e)); }
    finally { gate.current = false; setBusy(false); }
  };
  return { busy, notice, act };
}

function TermPicker({ terms, value, setValue, disabled = false }) {
  return <label className="mb-4 block max-w-md text-sm font-semibold">Academic term<select aria-label="Academic term" className="field mt-1" disabled={disabled} value={value} onChange={(e) => setValue(e.target.value)}><option value="">Select a term</option>{terms.map(t => <option key={t.id} value={t.id}>{t.name} ({t.starts_on} – {t.ends_on})</option>)}</select></label>;
}

function indiaNow() {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Kolkata', weekday: 'long', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(new Date());
  const value = type => parts.find(part => part.type === type)?.value;
  return { day: weekdays.indexOf(value('weekday')), minutes: Number(value('hour')) * 60 + Number(value('minute')) };
}

function indiaDate() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date());
  const value = type => parts.find(part => part.type === type)?.value;
  return `${value('year')}-${value('month')}-${value('day')}`;
}

function minutes(value) {
  const [hour, minute] = value.split(':').map(Number);
  return hour * 60 + minute;
}

function sectionDetails(section) {
  if (!section) return null;
  const match = section.match(/(?:^|\s)(\d)([A-Za-z]+)\s*\/\s*semester\s*(\d+)/i);
  return match ? `Section ${match[2]} · Semester ${match[3]}` : section;
}

function ClassCell({ entry, day, lock, busy }) {
  return <article className="rounded-md border border-blue-200 bg-blue-50 p-2.5 text-left text-xs leading-5 text-slate-700 shadow-sm">
    <p className="font-semibold text-slate-900">{entry.subject_code} · {entry.subject_name}</p>
    <p className="break-words">{entry.faculty}</p>
    <p className="break-words">{entry.room}</p>
    {entry.section && <p className="break-words text-slate-600">{sectionDetails(entry.section)}</p>}
    {lock && <button className="btn-secondary mt-2 !px-2 !py-1 text-xs" disabled={busy} onClick={() => lock(entry)} aria-label={`${entry.locked ? 'Unlock' : 'Lock'} ${entry.subject_code} ${days[day]} ${entry.start_time}`}>{entry.locked ? 'Unlock block' : 'Lock block'}</button>}
  </article>;
}

export function Weekly({ entries = [], periodDefinitions, lock, busy = false, selectedDate, dateEntries = [] }) {
  const now = indiaNow();
  const rows = configuredRows(periodDefinitions);
  return <div data-testid="timetable-scroll" className="card max-w-full overflow-x-auto">
    <table className="min-w-[1200px] w-full table-fixed border-collapse text-sm">
      <caption className="sr-only">Weekly timetable with time periods in rows and weekdays in columns</caption>
      <thead><tr>
        <th scope="col" className="sticky left-0 z-20 w-36 border border-slate-300 bg-slate-100 px-3 py-3 text-left font-bold text-slate-800">Time</th>
        <th scope="col" className="w-20 border border-slate-300 bg-slate-100 px-3 py-3 text-center font-bold text-slate-800">Period</th>
        {weekdays.map((day, index) => <th scope="col" key={day} className={`border border-slate-300 px-3 py-3 text-center font-bold ${now.day === index ? 'bg-blue-100 text-blue-900' : 'bg-slate-100 text-slate-800'}`} aria-current={now.day === index ? 'date' : undefined}>{day}</th>)}
      </tr></thead>
      <tbody>{rows.map(row => {
        if (row.break) return <tr key={`break-${row.start}`} className="bg-slate-100">
          <th scope="row" className="sticky left-0 z-10 border border-slate-300 bg-slate-200 px-3 py-2 text-left text-xs font-semibold text-slate-600">{row.start}–{row.end}</th>
          <td className="border border-slate-300 bg-slate-200 px-3 py-2 text-center text-xs font-semibold text-slate-600">—</td>
          <td colSpan={6} className="border border-slate-300 px-4 py-2 text-center text-xs font-medium uppercase tracking-wide text-slate-500">{row.label}</td>
        </tr>;
        const isCurrentPeriod = now.minutes >= minutes(row.start) && now.minutes < minutes(row.end);
        return <tr key={row.period_index}>
          <th scope="row" className={`sticky left-0 z-10 border border-slate-300 px-3 py-4 text-left align-top font-semibold ${isCurrentPeriod ? 'bg-amber-100 text-amber-900' : 'bg-white text-slate-800'}`}>{row.start}–{row.end}</th>
          <td className={`border border-slate-300 px-3 py-4 text-center align-top font-semibold ${isCurrentPeriod ? 'bg-amber-100 text-amber-900' : 'bg-white text-slate-800'}`}>{row.period_index + 1}</td>
          {weekdays.map((day, dayIndex) => {
            const cellEntries = entries.filter(entry => entry.day === dayIndex && entry.period_index === row.period_index);
            // Date entries are deliberately rendered only as a small marker. They
            // must never become a weekly, recurring assignment.
            const datedMarker = selectedDate && dateEntries.some(entry => (entry.date || entry.occurrence_date) === selectedDate
              && entry.day === dayIndex && entry.period_index === row.period_index
              && (entry.dated_coverage || entry.dated_change || entry.kind));
            const current = isCurrentPeriod && now.day === dayIndex;
            const label = cellEntries.length ? cellEntries.map(entry => `${entry.subject_code} ${entry.subject_name}, ${entry.faculty}, ${entry.room}${entry.section ? `, ${sectionDetails(entry.section)}` : ''}`).join('; ') : 'No class';
            return <td key={day} aria-label={`${day} ${row.start}–${row.end}: ${label}`} className={`min-w-[160px] border border-slate-300 p-2 align-top ${current ? 'bg-amber-50 ring-2 ring-inset ring-amber-400' : cellEntries.length ? 'bg-white' : now.day === dayIndex ? 'bg-blue-50' : 'bg-slate-50'}`}>
              {cellEntries.length ? <div className="space-y-2">{cellEntries.map(entry => <ClassCell key={entry.id ?? `${entry.requirement_id}-${entry.occurrence}`} entry={entry} day={dayIndex} lock={lock} busy={busy} />)}</div> : <span className="block py-3 text-center text-slate-400" aria-hidden="true">—</span>}
              {datedMarker && <span className="mt-1 inline-block rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-semibold text-amber-900">Dated change</span>}
            </td>;
          })}
        </tr>;
      })}</tbody>
    </table>
  </div>;
}

function ClassCard({ title, entry, empty }) {
  return <section className={`card border-l-4 p-5 ${entry?.dated_coverage ? 'border-amber-500 bg-amber-50' : 'border-primary'}`}><h2 className="text-sm font-semibold text-muted">{title}</h2>{entry ? <>{entry.dated_coverage && <p className="mt-2 inline-block rounded bg-amber-200 px-2 py-1 text-sm font-bold text-amber-950">Substitute class</p>}<p className="mt-2 text-xl font-bold">{entry.subject_name}</p><p>{entry.subject_code} · {entry.room}</p><p className="text-sm">{entry.dated_coverage ? `Original faculty: ${entry.original_faculty || entry.faculty}` : entry.faculty} · {entry.section}</p><p className="mt-2 font-semibold">{entry.date} · {entry.start_time}–{entry.end_time}</p></> : <p className="mt-2">{empty}</p>}</section>;
}

function DateSchedule({ date, entries = [], empty }) {
  return <section className="card my-4 p-4" aria-label="Date-specific schedule"><div className="flex flex-wrap items-end justify-between gap-3"><div><h2 className="font-bold">Selected date schedule</h2><p className="mt-1 text-sm text-muted">{date} · Asia/Kolkata</p></div></div>{entries.length ? <div className="mt-3 grid gap-3 lg:grid-cols-2">{entries.map(entry => <article className={`rounded-lg border p-3 ${entry.dated_coverage ? 'border-amber-400 bg-amber-50' : 'bg-slate-50'}`} key={`${entry.id}:${entry.date}`}><div className="flex flex-wrap items-center gap-2">{entry.dated_coverage && <span className="rounded bg-amber-200 px-2 py-1 text-xs font-bold text-amber-950">Substitute class</span>}<p className="font-semibold">{entry.start_time}–{entry.end_time}</p></div><p className="mt-2 font-bold">{entry.subject_code} · {entry.subject_name}</p><p className="text-sm">Room: {entry.room}</p><p className="text-sm">{entry.dated_coverage ? `Original faculty: ${entry.original_faculty || entry.faculty}` : `Faculty: ${entry.faculty}`}</p><p className="text-sm">{sectionDetails(entry.section)}</p></article>)}</div> : <p className="mt-3 text-sm text-muted">{empty || 'No classes scheduled for this date.'}</p>}</section>;
}

function FacultyRescheduleRequest({ entries }) {
  const { token } = useAuth(); const { busy, notice, act } = useAction();
  const [entryId, setEntryId] = useState(''); const [date, setDate] = useState('');
  const [preview, setPreview] = useState(null);
  const submit = event => { event.preventDefault(); act(async () => {
    const result = await request('/timetable/operations/preview', { token, body: {
      action: 'preview_replacement_slot', entry_id: Number(entryId), occurrence_date: date,
    } }); setPreview(result);
  }, 'Reschedule request preview submitted for department review.'); };
  return <section className="card mt-5 p-4"><h2 className="text-lg font-bold">Request a class reschedule</h2><p className="mt-1 text-sm text-muted">MAWOS finds a conflict-free option. Your HOD must review and confirm it before the published occurrence changes.</p><form className="mt-3 grid min-w-0 gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]" onSubmit={submit}><label className="min-w-0 text-sm">Assigned class<select aria-label="Assigned class" className="field mt-1" required value={entryId} onChange={event => setEntryId(event.target.value)}><option value="">Choose…</option>{entries.map(entry => <option value={entry.id} key={entry.id}>{entry.subject_code} · {entry.day_name} · {entry.start_time}</option>)}</select></label><label className="min-w-0 text-sm">Occurrence date<input aria-label="Occurrence date" className="field mt-1" type="date" required value={date} onChange={event => setDate(event.target.value)} /></label><button className="btn-primary self-end" disabled={busy}>Find replacement</button></form><Notice text={notice} />{preview && <div className="mt-3 rounded-lg border bg-slate-50 p-3 text-sm"><p className="font-semibold">Proposed: {preview.summary.replacement_date}, period {preview.summary.period_index + 1}, {preview.summary.room}</p><p className="mt-1 break-all text-xs text-muted">Request {preview.preview_id} · correlation {preview.correlation_id}</p></div>}</section>;
}

function PendingOperations({ onChanged = () => {} }) {
  const { token } = useAuth(); const [revision, setRevision] = useState(0);
  const state = useApi(() => Promise.all([
    request('/timetable/operations/pending', { token }),
    request('/timetable/operations/audit', { token }),
  ]), [token, revision]);
  const { busy, notice, act } = useAction();
  if (state.loading) return <LoadingSkeleton />;
  if (state.error) return <ErrorState error={state.error} />;
  const [pending, audit] = state.data;
  return <section className="card my-4 min-w-0 p-4"><h2 className="text-lg font-bold">Timetable operations</h2><p className="mt-1 text-sm text-muted">Agent requests remain previews until an authorized reviewer confirms them. Confirmation rechecks current conflicts.</p><div className="mt-3 grid min-w-0 gap-3 lg:grid-cols-2">{pending.length ? pending.map(item => <article className="min-w-0 rounded-xl border border-amber-200 bg-amber-50 p-3" key={item.preview_id}><p className="break-words font-semibold">{item.action.replaceAll('_', ' ')} · {item.department}</p><p className="mt-1 break-words text-sm">{item.summary.message || 'Validated timetable operation preview'}</p><p className="mt-1 break-all text-xs text-muted">{item.correlation_id}</p><div className="mt-3 flex flex-wrap gap-2"><button className="btn-primary" disabled={busy} onClick={() => act(async () => { await request('/timetable/operations/confirm', { token, body: { preview_id: item.preview_id } }); setRevision(value => value + 1); onChanged(); }, 'Operation confirmed and audited.')}>Confirm reviewed change</button><button className="btn-secondary" disabled={busy} onClick={() => act(async () => { await request(`/timetable/operations/previews/${item.preview_id}/discard`, { token, body: {} }); setRevision(value => value + 1); onChanged(); }, 'Operation preview discarded.')}>Discard</button></div></article>) : <p className="text-sm text-muted">No pending operation previews.</p>}</div><details className="mt-4"><summary className="cursor-pointer font-semibold">Audit history</summary><div className="mt-2 space-y-2">{audit.slice(0, 20).map(item => <p className="break-words rounded-lg bg-slate-50 p-2 text-xs" key={item.id}>{item.phase} · {item.action} · {item.department} · {item.correlation_id}</p>)}</div></details><Notice text={notice} /></section>;
}

function InstitutionActionCard({ item, run, onChanged = () => {} }) {
  const { token } = useAuth(); const { busy, notice, act } = useAction(); const [preview, setPreview] = useState(null); const [conflicts, setConflicts] = useState(null);
  const makePreview = action => act(async () => setPreview(await request('/timetable/operations/preview', { token, body: {
    action, department: item.department, ...(action === 'generate_timetable_draft' ? { term_id: item.term_id } : { run_id: item.latest_run_id }),
  } })), 'Operation preview ready.');
  const confirm = () => act(async () => { await request('/timetable/operations/confirm', { token, body: {
    preview_id: preview.preview_id, confirmation_token: preview.confirmation_token,
  } }); setPreview(null); onChanged(); }, 'Operation confirmed and audited.');
  const readConflicts = () => act(async () => setConflicts(await request('/timetable/operations/preview', { token, body: { action: 'get_timetable_conflicts', run_id: item.latest_run_id } })), 'Conflict report loaded.');
  return <section className="card min-w-0 p-4"><h2 className="font-bold">{item.department} · {item.term}</h2><p>{item.published_run_id ? `Published version ${item.published_run_id}` : 'No published timetable'}</p>{item.published_metrics && <p>{item.published_metrics.placed} / {item.published_metrics.required} periods covered</p>}<p className="text-sm text-muted">Latest run: {item.latest_run_id ? `version ${item.latest_run_id} · ` : ''}{item.latest_status}</p>{run && <><p className="mt-2 text-sm">Draft status: <strong>Version {run.id} · {run.status}</strong>{run.metrics ? ` · ${run.metrics.placed} / ${run.metrics.required} periods placed` : ''}</p><div className="mt-2 rounded-lg border bg-slate-50 p-3 text-sm" aria-label="Current draft conflict report"><p className="font-semibold">Conflict report</p>{run.conflicts?.length ? <ul className="mt-1 list-disc pl-5">{run.conflicts.map((conflict, index) => <li key={index}>{conflict.message}</li>)}</ul> : <p className="mt-1">No hard conflicts reported.</p>}{run.unplaced?.length ? <p className="mt-1">{run.unplaced.length} requirement(s) remain unplaced.</p> : null}</div></>}<div className="mt-3 flex flex-wrap gap-2"><button className="btn-secondary" disabled={busy || preview} onClick={() => makePreview('generate_timetable_draft')}>Preview draft generation</button><button className="btn-secondary" disabled={busy || !item.latest_run_id} onClick={readConflicts}>View conflict report</button><button className="btn-secondary" disabled={busy || preview || !item.latest_run_id || !['DRAFT', 'PARTIAL'].includes(item.latest_status)} onClick={() => makePreview('validate_timetable_draft')}>Preview validation</button><button className="btn-secondary" disabled={busy || preview || !item.latest_run_id || item.latest_status !== 'COMPLETE'} onClick={() => makePreview('publish_timetable_draft')}>Preview publication</button></div>{conflicts && <div className="mt-3 rounded-lg border bg-slate-50 p-3 text-sm" aria-label="Conflict report"><p className="font-semibold">Conflict report · Version {conflicts.run_id} · {conflicts.status}</p>{conflicts.conflicts?.length ? <ul className="mt-2 list-disc pl-5">{conflicts.conflicts.map((conflict, index) => <li key={index}>{conflict.message}</li>)}</ul> : <p className="mt-1">No hard conflicts reported.</p>}{conflicts.unplaced?.length ? <p className="mt-1">{conflicts.unplaced.length} requirement(s) remain unplaced.</p> : null}</div>}{preview && <div className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm"><p>{preview.summary.message || `${preview.action.replaceAll('_', ' ')} is ready.`}</p><p className="mt-1 break-all text-xs text-muted">{preview.correlation_id}</p><div className="mt-2 flex flex-wrap gap-2"><button className="btn-primary" disabled={busy} onClick={confirm}>Confirm operation</button><button className="btn-secondary" disabled={busy} onClick={() => setPreview(null)}>Discard</button></div></div>}<Notice text={notice} /></section>;
}

function OccurrenceOperations({ run, department }) {
  const { token } = useAuth(); const { busy, notice, act } = useAction();
  const [entryId, setEntryId] = useState(''); const [date, setDate] = useState(''); const [replacementDate, setReplacementDate] = useState(''); const [periodIndex, setPeriodIndex] = useState(''); const [roomId, setRoomId] = useState(''); const [preview, setPreview] = useState(null);
  const entries = run?.status === 'PUBLISHED' ? draftEntries(run) : [];
  const makePreview = (action) => act(async () => {
    const body = { action, ...(department ? { department } : {}), entry_id: Number(entryId), occurrence_date: date };
    if (action === 'preview_reschedule_class') Object.assign(body, { replacement_date: replacementDate, period_index: Number(periodIndex), room_id: Number(roomId) });
    setPreview(await request('/timetable/operations/preview', { token, body }));
  }, 'Operation preview ready. Review it before confirmation.');
  const confirm = () => act(async () => { await request('/timetable/operations/confirm', { token, body: { preview_id: preview.preview_id, confirmation_token: preview.confirmation_token } }); setPreview(null); }, 'Authorized occurrence change confirmed and audited.');
  return <section className="card my-4 min-w-0 p-4"><h2 className="text-lg font-bold">Published class changes</h2><p className="mt-1 text-sm text-muted">A dated class is never changed directly. Select a published class, create a preview, then explicitly confirm it.</p>{!entries.length ? <p className="mt-3 text-sm text-muted">Load a published timetable version for this scope to preview a reschedule, replacement, or cancellation.</p> : <><div className="mt-3 grid gap-3 lg:grid-cols-2"><label className="text-sm">Published class<select aria-label="Published class" className="field mt-1" value={entryId} onChange={event => setEntryId(event.target.value)}><option value="">Choose a class</option>{entries.map(entry => <option value={entry.id} key={entry.id}>{entry.subject_code} · {entry.section} · {weekdays[entry.day]} period {entry.period_index + 1}</option>)}</select></label><label className="text-sm">Occurrence date<input aria-label="Operation occurrence date" className="field mt-1" type="date" value={date} onChange={event => setDate(event.target.value)} /></label></div><div className="mt-3 flex flex-wrap gap-2"><button className="btn-secondary" disabled={busy || !entryId || !date} onClick={() => makePreview('preview_replacement_slot')}>Find replacement slot</button><button className="btn-secondary" disabled={busy || !entryId || !date} onClick={() => makePreview('preview_cancel_class')}>Preview cancellation</button></div><details className="mt-3"><summary className="cursor-pointer font-semibold">Specify a replacement slot</summary><div className="mt-3 grid gap-3 sm:grid-cols-3"><label className="text-sm">Replacement date<input aria-label="Replacement date" className="field mt-1" type="date" value={replacementDate} onChange={event => setReplacementDate(event.target.value)} /></label><label className="text-sm">Period index<input aria-label="Replacement period index" className="field mt-1" type="number" min="0" max="23" value={periodIndex} onChange={event => setPeriodIndex(event.target.value)} /></label><label className="text-sm">Compatible room ID<input aria-label="Replacement room ID" className="field mt-1" type="number" min="1" value={roomId} onChange={event => setRoomId(event.target.value)} /></label></div><button className="btn-secondary mt-3" disabled={busy || !entryId || !date || !replacementDate || periodIndex === '' || !roomId} onClick={() => makePreview('preview_reschedule_class')}>Preview specified reschedule</button></details></>}{preview && <div className="mt-4 rounded-xl border border-amber-300 bg-amber-50 p-4" aria-label="Occurrence change confirmation"><h3 className="font-bold">Review proposed change</h3><p className="mt-1 text-sm">{preview.summary.message}</p>{preview.summary.replacement_date && <p className="mt-1 text-sm">Replacement: {preview.summary.replacement_date}, period {preview.summary.period_index + 1} · {preview.summary.room}</p>}<p className="mt-1 break-all text-xs text-muted">Correlation ID: {preview.correlation_id}</p><div className="mt-3 flex flex-wrap gap-2"><button className="btn-primary" disabled={busy} onClick={confirm}>Confirm reviewed change</button><button className="btn-secondary" disabled={busy} onClick={() => setPreview(null)}>Discard preview</button></div></div>}<Notice text={notice} /></section>;
}

function ScopedOperations({ item, onChanged }) {
  const { token } = useAuth();
  const latest = useApi(() => item.latest_run_id ? request(`/timetable/runs/${item.latest_run_id}`, { token }) : Promise.resolve(null), [token, item.latest_run_id]);
  const published = useApi(() => item.published_run_id ? request(`/timetable/runs/${item.published_run_id}`, { token }) : Promise.resolve(null), [token, item.published_run_id]);
  return <><InstitutionActionCard item={item} run={latest.data} onChanged={onChanged} />{latest.error && <ErrorState error={latest.error} />}{published.error && <ErrorState error={published.error} />}<OccurrenceOperations run={published.data} department={item.department} /></>;
}

export function PersonalTimetable({ role, onRefreshScheduled }) {
  const { token } = useAuth();
  const [selectedDate, setSelectedDate] = useState(() => indiaDate());
  const [{ data, loading, error }, setState] = useState({ data: null, loading: true, error: null });
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setState({ data: null, loading: true, error: null });
    const refresh = async () => {
      try {
        // Keep the default request backward compatible; a changed date explicitly
        // asks the server for the exact Asia/Kolkata occurrence schedule.
        const path = selectedDate === indiaDate() ? `/${role}/timetable`
          : `/${role}/timetable?date=${encodeURIComponent(selectedDate)}`;
        const result = await request(path, { token, signal: controller.signal });
        if (active) setState({ data: result, loading: false, error: null });
      } catch (error) {
        if (active) setState({ data: null, loading: false, error });
      }
    };
    refresh();
    onRefreshScheduled?.(refresh);
    const timer = setInterval(refresh, 30000);
    return () => { active = false; controller.abort(); clearInterval(timer); };
  }, [token, role, selectedDate, onRefreshScheduled]);
  if (loading) return <LoadingSkeleton />;
  if (error) return <ErrorState error={error} />;
  const dateEntries = data.date_schedule || data.today || [];
  const weeklyDateMarkers = [...dateEntries, ...(data.selected_changes || [])];
  return <><PageHeader title="My timetable" eyebrow="Published academic schedule · Asia/Kolkata" /><label className="mb-4 block max-w-xs text-sm font-semibold">Selected date<input aria-label="Selected date" className="field mt-1" type="date" value={selectedDate} onChange={event => setSelectedDate(event.target.value)} /></label>{data.changes?.length > 0 && <section className="mb-4 rounded-xl border border-amber-300 bg-amber-50 p-4" aria-label="Authorized timetable changes"><h2 className="font-bold">Schedule changes</h2>{data.changes.map(change => <p className="mt-2 break-words text-sm" key={`${change.kind || 'change'}-${change.id}`}>{change.subject_code} · {change.occurrence_date} · {change.substitute_class ? 'Coverage / Substitute class accepted' : change.status}{change.replacement_date ? ` → ${change.replacement_date}, period ${change.replacement_period_index + 1}` : change.substitute_class ? '' : ' · make-up class required'}</p>)}</section>}<div className="grid gap-4 md:grid-cols-2"><ClassCard title="Current class" entry={data.current} empty={data.message} /><ClassCard title="Next class" entry={data.next} empty={data.next_message || 'No remaining published classes.'} /></div><DateSchedule date={data.selected_date || selectedDate} entries={dateEntries} empty={data.selected_date === selectedDate ? undefined : data.message} /><h2 className="mb-3 text-lg font-bold">Weekly timetable</h2>{data.published ? <Weekly entries={data.weekly} periodDefinitions={data.period_definitions} selectedDate={data.selected_date || selectedDate} dateEntries={weeklyDateMarkers} /> : <p className="card p-4">No published timetable for the current academic term.</p>}{role === 'faculty' && <><FacultyRescheduleRequest entries={data.weekly} /><Availability /></>}</>;
}

function Availability() {
  const { token } = useAuth();
  const terms = useApi(() => request('/timetable/terms', { token }), [token]);
  const [term, setTerm] = useState('');
  return <section className="card mt-5 p-4"><h2 className="mb-3 text-lg font-bold">My availability</h2><p className="mb-3 text-sm text-muted">Mark unavailable periods before your HOD publishes the term. Published term inputs are fixed.</p>{terms.error ? <ErrorState error={terms.error} /> : <TermPicker terms={terms.data || []} value={term} setValue={setTerm} />}{term && <AvailabilityEditor key={term} term={term} />}</section>;
}

function AvailabilityEditor({ term, room = null, periods: supplied = null, initial = [], onSaved }) {
  const { token } = useAuth();
  const url = room ? `/timetable/terms/${term}/rooms/${room}/availability` : `/faculty/timetable/terms/${term}/availability`;
  const { data, loading, error } = useApi(() => room ? Promise.resolve({ periods: supplied, unavailable_period_ids: initial }) : request(url, { token }), [token, term, room]);
  const [selected, setSelected] = useState([]);
  const { busy, notice, act } = useAction();
  useEffect(() => { if (data) setSelected(data.unavailable_period_ids); }, [data]);
  if (loading) return <LoadingSkeleton />;
  if (error) return <ErrorState error={error} />;
  return <><div className="grid max-h-72 gap-2 overflow-auto sm:grid-cols-3">{data.periods.filter(p => !p.is_break && !p.is_closed).map(p => <label key={p.id} className="flex items-center gap-2 text-sm"><input type="checkbox" disabled={busy} checked={selected.includes(p.id)} onChange={e => setSelected(old => e.target.checked ? [...old, p.id] : old.filter(id => id !== p.id))} />{days[p.day_of_week]} {p.starts_at.slice(0, 5)}–{p.ends_at.slice(0, 5)}</label>)}</div><button className="btn-primary mt-3" disabled={busy} onClick={() => act(async () => { await request(url, { token, method: 'PUT', body: { unavailable_period_ids: selected } }); onSaved?.(); })}>{busy ? 'Saving…' : 'Save availability'}</button><Notice text={notice} /></>;
}

function ConfigForm({ title, fields, save, disabled = false }) {
  const { busy, notice, act } = useAction();
  return <details className="card my-3 p-4"><summary className="cursor-pointer font-semibold">{title}</summary><form className="mt-3 grid gap-3 sm:grid-cols-2" onSubmit={e => { e.preventDefault(); const values = new FormData(e.currentTarget); const body = Object.fromEntries(fields.map(f => [f.name, f.type === 'number' || f.numeric ? Number(values.get(f.name)) : values.get(f.name)])); act(() => save(body)); }}>{fields.map(f => <label className="text-sm" key={f.name}>{f.label}{f.options ? <select className="field mt-1" name={f.name} required disabled={busy || disabled} defaultValue=""><option value="">Choose…</option>{f.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select> : <input className="field mt-1" name={f.name} type={f.type || 'text'} min={f.min} max={f.max} defaultValue={f.defaultValue} required disabled={busy || disabled} />}</label>)}<button className="btn-primary self-end" disabled={busy || disabled}>{busy ? 'Saving…' : `Save ${title.toLowerCase()}`}</button></form><Notice text={notice} /></details>;
}
const number = (name, label, defaultValue, max = 168) => ({ name, label, type: 'number', min: 1, max, defaultValue });
const options = (name, label, rows, labeler, numeric = true) => ({ name, label, numeric, options: rows.map(r => ({ value: r.id ?? r.code, label: labeler(r) })) });

function DepartmentConfig({ term, refresh, disabled }) {
  const { token } = useAuth();
  const [revision, setRevision] = useState(0);
  const save = (path, method = 'POST') => async body => { await request(path, { token, method, body }); setRevision(r => r + 1); };
  const latest = useApi(() => request(`/hod/timetable/terms/${term}/configuration`, { token }), [token, term, revision, refresh]);
  if (latest.loading) return <LoadingSkeleton />;
  if (latest.error) return <ErrorState error={latest.error} />;
  const cfg = latest.data;
  const prefix = `/hod/timetable/terms/${term}`;
  return <details className="my-4" open={!cfg.requirements.length}><summary className="cursor-pointer text-lg font-bold">Department configuration</summary><p className="my-2 text-sm text-muted">{cfg.sections.length} sections · {cfg.requirements.length} requirements · {cfg.rooms.length} rooms. Review staffing, qualifications and load limits before generation.</p>
    <ConfigForm title="Section" disabled={disabled} fields={[number('year', 'Year', 3, 4), number('semester', 'Semester', 5, 8), { name: 'name', label: 'Section name', defaultValue: 'A' }, number('size', 'Student capacity', 60, 10000)]} save={save(`${prefix}/sections`)} />
    <MappedAssignment cfg={cfg} disabled={disabled} save={save('/hod/timetable/assignments')} />
    <MappedQualification cfg={cfg} disabled={disabled} save={save('/hod/timetable/qualifications')} />
    <ConfigForm title="Faculty load limits" disabled={disabled} fields={[options('faculty_id', 'Faculty', cfg.faculty, r => r.name), number('daily_limit', 'Daily limit', 5, 24), number('weekly_limit', 'Weekly limit', 24)]} save={save(`${prefix}/faculty-limits`, 'PUT')} />
    <MappedRequirement cfg={cfg} disabled={disabled} save={save(`${prefix}/requirements`)} />
    <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr><th>Section</th><th>Subject</th><th>Weekly / daily</th><th>Block / room</th></tr></thead><tbody>{cfg.requirements.map(r => <tr className="border-t" key={r.id}><td className="py-2">{cfg.sections.find(s => s.id === r.section_id)?.name}</td><td>{cfg.assignments.find(a => a.id === r.assignment_id)?.subject_code}</td><td>{r.periods_per_week} / {r.max_per_day}</td><td>{r.block_length} / {r.room_type}</td></tr>)}</tbody></table></div>
    <details className="card my-3 p-4"><summary className="font-semibold">Qualifications and load limits</summary>{cfg.faculty.map(f => <p className="mt-2 text-sm" key={f.id}>{f.name}: {cfg.qualifications.filter(q => q.faculty_id === f.id).map(q => q.subject_code).join(', ') || 'No qualifications configured'} · daily {cfg.limits.find(l => l.faculty_id === f.id)?.daily_limit ?? 'unset'}, weekly {cfg.limits.find(l => l.faculty_id === f.id)?.weekly_limit ?? 'unset'}</p>)}</details>
    {!disabled && cfg.rooms.map(room => <details className="card my-3 p-4" key={room.id}><summary className="font-semibold">Room availability · {room.name} ({room.kind}, {room.capacity} seats)</summary><AvailabilityEditor term={term} room={room.id} periods={cfg.periods} initial={room.unavailable_period_ids} /></details>)}
  </details>;
}

function useSectionMapping(cfg) {
  const [sectionId, setSectionId] = useState(cfg.sections[0]?.id ? String(cfg.sections[0].id) : '');
  const section = cfg.sections.find(s => String(s.id) === sectionId);
  const subjects = section ? cfg.subjects.filter(s => s.semester === section.semester) : [];
  const [subjectCode, setSubjectCode] = useState('');
  useEffect(() => { setSubjectCode(''); }, [sectionId]);
  const exact = section && subjectCode ? cfg.assignments.filter(a => a.year === section.year && a.section === section.name && a.subject_code === subjectCode) : [];
  const elsewhere = subjectCode ? cfg.assignments.filter(a => a.subject_code === subjectCode && !exact.includes(a)) : [];
  const subject = cfg.subjects.find(s => s.code === subjectCode);
  const mismatch = subjectCode && !exact.length && elsewhere.length
    ? `${subject?.name || subjectCode} (${subjectCode}) is assigned to ${elsewhere.map(a => `Year ${a.year} Section ${a.section}`).join(', ')} in the database, not Year ${section.year} Section ${section.name}.`
    : subjectCode && !exact.length ? `${subject?.name || subjectCode} (${subjectCode}) has no authorized teaching assignment for Year ${section.year} Section ${section.name}.` : '';
  return { sectionId, setSectionId, section, subjects, subjectCode, setSubjectCode, exact, mismatch };
}

function MappingFields({ cfg, mapping, disabled }) {
  const faculty = mapping.exact.map(a => cfg.faculty.find(f => f.id === a.faculty_id)).filter(Boolean);
  return <><label className="text-sm">Section<select aria-label="Mapped section" className="field mt-1" disabled={disabled} value={mapping.sectionId} onChange={e => mapping.setSectionId(e.target.value)}><option value="">Choose…</option>{cfg.sections.map(s => <option key={s.id} value={s.id}>Year {s.year} · semester {s.semester} · {s.name}</option>)}</select></label><label className="text-sm">Subject<select aria-label="Mapped subject" className="field mt-1" disabled={disabled || !mapping.section} value={mapping.subjectCode} onChange={e => mapping.setSubjectCode(e.target.value)}><option value="">Choose…</option>{mapping.subjects.map(s => <option key={s.code} value={s.code}>{s.name} ({s.code})</option>)}</select></label><label className="text-sm">Authorized faculty<select aria-label="Mapped faculty" className="field mt-1" disabled={disabled || !mapping.exact.length} value={mapping.exact[0]?.faculty_id || ''} onChange={() => {}}><option value="">No authorized faculty</option>{faculty.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}</select></label>{mapping.mismatch && <p role="alert" className="sm:col-span-2 text-sm text-amber-700">{mapping.mismatch}</p>}</>;
}

function MappedAssignment({ cfg, save, disabled }) {
  const mapping = useSectionMapping(cfg);
  const { busy, notice, act } = useAction();
  const assignment = mapping.exact[0];
  return <details className="card my-3 p-4"><summary className="cursor-pointer font-semibold">Teaching assignment</summary><div className="mt-3 grid gap-3 sm:grid-cols-2"><MappingFields cfg={cfg} mapping={mapping} disabled={busy || disabled} /><button className="btn-primary self-end" disabled={busy || disabled || !assignment} onClick={() => act(() => save({ subject_code: assignment.subject_code, faculty_id: assignment.faculty_id, year: assignment.year, section: assignment.section }))}>{busy ? 'Saving…' : 'Confirm existing assignment'}</button></div><Notice text={notice} /></details>;
}

function MappedQualification({ cfg, save, disabled }) {
  const mapping = useSectionMapping(cfg);
  const { busy, notice, act } = useAction();
  const assignment = mapping.exact[0];
  return <details className="card my-3 p-4"><summary className="cursor-pointer font-semibold">Qualification</summary><div className="mt-3 grid gap-3 sm:grid-cols-2"><MappingFields cfg={cfg} mapping={mapping} disabled={busy || disabled} /><button className="btn-primary self-end" disabled={busy || disabled || !assignment} onClick={() => act(() => save({ faculty_id: assignment.faculty_id, subject_code: assignment.subject_code }))}>{busy ? 'Saving…' : 'Save authorized qualification'}</button></div><Notice text={notice} /></details>;
}

function MappedRequirement({ cfg, save, disabled }) {
  const mapping = useSectionMapping(cfg);
  const { busy, notice, act } = useAction();
  const assignment = mapping.exact[0];
  return <details className="card my-3 p-4"><summary className="cursor-pointer font-semibold">Teaching requirement</summary><form className="mt-3 grid gap-3 sm:grid-cols-2" onSubmit={e => { e.preventDefault(); const values = new FormData(e.currentTarget); act(() => save({ section_id: Number(mapping.sectionId), assignment_id: assignment.id, periods_per_week: Number(values.get('periods_per_week')), max_per_day: Number(values.get('max_per_day')), block_length: Number(values.get('block_length')), room_type: values.get('room_type'), priority: Number(values.get('priority')) })); }}><MappingFields cfg={cfg} mapping={mapping} disabled={busy || disabled} />{[number('periods_per_week', 'Periods per week', 4), number('max_per_day', 'Maximum per day', 2, 24), number('block_length', 'Consecutive block length', 1, 24), { name: 'room_type', label: 'Required room type (or any)', defaultValue: 'classroom' }, number('priority', 'Priority', 1, 10)].map(f => <label className="text-sm" key={f.name}>{f.label}<input className="field mt-1" name={f.name} type={f.type || 'text'} min={f.min} max={f.max} defaultValue={f.defaultValue} required disabled={busy || disabled} /></label>)}<button className="btn-primary self-end" disabled={busy || disabled || !assignment}>{busy ? 'Saving…' : 'Save teaching requirement'}</button></form><Notice text={notice} /></details>;
}

function BootstrapPanel({ term, onApplied }) {
  const { token } = useAuth();
  const { busy, notice, act } = useAction();
  const [preview, setPreview] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const [fixedWeeklyPeriods, setFixedWeeklyPeriods] = useState('');
  const [useSubjectCredits, setUseSubjectCredits] = useState(false);
  const [facultyDailyLimit, setFacultyDailyLimit] = useState('');
  const [facultyWeeklyLimit, setFacultyWeeklyLimit] = useState('');
  const planningOptions = () => ({
    term_id: Number(term),
    fixed_weekly_periods: fixedWeeklyPeriods ? Number(fixedWeeklyPeriods) : null,
    use_subject_credits: useSubjectCredits,
    faculty_daily_limit: facultyDailyLimit ? Number(facultyDailyLimit) : null,
    faculty_weekly_limit: facultyWeeklyLimit ? Number(facultyWeeklyLimit) : null,
  });
  const applyBody = () => ({
    ...planningOptions(), apply: true, confirm_apply: confirmed,
    preview_hash: preview?.preview_hash,
  });
  useEffect(() => { setPreview(null); setConfirmed(false); }, [fixedWeeklyPeriods, useSubjectCredits, facultyDailyLimit, facultyWeeklyLimit]);
  const previewNow = () => act(async () => { setPreview(await request(`/timetable/terms/${term}/bootstrap`, { token, body: planningOptions() })); }, 'Dry-run preview loaded. No data was written.');
  const applyNow = () => act(async () => { const result = await request(`/timetable/terms/${term}/bootstrap`, { token, body: applyBody() }); setPreview(result); setConfirmed(false); onApplied(); }, 'Existing department data loaded. Review readiness before generation.');
  return <section className="card my-4 p-4"><h2 className="font-bold">Load existing department data</h2><p className="mt-1 text-sm text-muted">Preview enrolled cohorts and authorized teaching assignments first. Existing academic rows and timetable versions are never changed.</p><div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><label className="text-sm">Fixed weekly periods (optional)<input aria-label="Bootstrap weekly periods" className="field mt-1" type="number" min="1" max="168" disabled={busy} value={fixedWeeklyPeriods} onChange={event => setFixedWeeklyPeriods(event.target.value)} /></label><label htmlFor="use-subject-credits" className="flex cursor-pointer items-center gap-2 text-sm"><input id="use-subject-credits" type="checkbox" checked={useSubjectCredits} onChange={(event) => setUseSubjectCredits(event.target.checked)} />Use subject credits explicitly</label><label className="text-sm">Faculty daily limit (optional)<input aria-label="Bootstrap faculty daily limit" className="field mt-1" type="number" min="1" max="24" disabled={busy} value={facultyDailyLimit} onChange={event => setFacultyDailyLimit(event.target.value)} /></label><label className="text-sm">Faculty weekly limit (optional)<input aria-label="Bootstrap faculty weekly limit" className="field mt-1" type="number" min="1" max="168" disabled={busy} value={facultyWeeklyLimit} onChange={event => setFacultyWeeklyLimit(event.target.value)} /></label></div><div className="mt-3 flex flex-wrap items-center gap-3"><button className="btn-secondary" disabled={busy || Boolean(facultyDailyLimit) !== Boolean(facultyWeeklyLimit)} onClick={previewNow}>{busy ? 'Working…' : 'Preview dry run'}</button>{preview && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} />I reviewed this preview and confirm the import</label>}<button className="btn-primary" disabled={busy || !preview || !confirmed} onClick={applyNow}>Apply previewed changes</button></div><Notice text={notice} />{preview && <div className="mt-4 text-sm"><p className="font-semibold">{preview.departments.length} department · {preview.sections.length} sections · {preview.faculty_assignments.length} assignments</p><p>{preview.requirements.filter(r => r.state === 'would_create' || r.state === 'created').length} requirements ready to create · {preview.missing_weekly_period_values.length} missing weekly values · {preview.missing_rooms.length} missing rooms</p>{preview.defaulted_values.length > 0 && <p>{preview.defaulted_values.length} explicitly/default configured values are listed in this preview.</p>}<h3 className="mt-2 font-semibold">Conflicts that prevent generation</h3>{preview.conflicts.length ? <ul className="list-disc pl-5">{preview.conflicts.map((c, i) => <li key={`${c.code}-${i}`}>{c.message}</li>)}</ul> : <p>No bootstrap conflicts reported.</p>}</div>}</section>;
}

function draftEntries(run) {
  const meta = run.configuration.metadata;
  const periods = run.configuration.data.periods;
  return run.entries.map(e => {
    const p = periods.find(p => p.day === e.day && p.index === e.period_index);
    const section = meta.sections[e.section_id];
    const time = n => `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}`;
    return { ...e, subject_code: e.subject, subject_name: meta.subjects[e.subject], faculty: meta.faculty[e.faculty_id], room: meta.rooms[e.room_id], section: `${section.year}${section.name} / semester ${section.semester}`, start_time: time(p.start), end_time: time(p.end) };
  });
}

export function HodTimetable() {
  const { token } = useAuth();
  const terms = useApi(() => request('/timetable/terms', { token }), [token]);
  const [term, setTerm] = useState('');
  if (terms.loading) return <LoadingSkeleton />;
  if (terms.error) return <ErrorState error={terms.error} />;
  return <><PageHeader title="Department timetable" eyebrow="HOD / Draft, validate and publish" /><TermPicker terms={terms.data} value={term} setValue={setTerm} />{!terms.data.length && <p className="card p-4">Ask an administrator to configure the academic term, periods and department rooms.</p>}{term && <HodWorkspace key={`${token}:${term}`} term={term} />}</>;
}

function HodWorkspace({ term }) {
  const { token } = useAuth();
  const { busy, notice, act } = useAction();
  const [ready, setReady] = useState(null);
  const [run, setRun] = useState(null);
  const [seed, setSeed] = useState(7);
  const [refresh, setRefresh] = useState(0);
  const [section, setSection] = useState('');
  const [publishPreview, setPublishPreview] = useState(null);
  const history = useApi(() => request(`/timetable/runs?term_id=${term}`, { token }), [token, term, refresh]);
  const loadRun = async id => { const r = await request(`/timetable/runs/${id}`, { token }); setRun(r); setSection(''); };
  const action = (name, body, method = 'POST') => act(async () => { setRun(await request(`/hod/timetable/runs/${run.id}/${name}`, { token, method, body })); setRefresh(r => r + 1); }, 'Draft updated.');
  const previewPublish = () => act(async () => {
    setPublishPreview(await request('/timetable/operations/preview', { token, body: { action: 'publish_timetable_draft', run_id: run.id } }));
  }, 'Publication preview ready. Review it before confirmation.');
  const confirmPublish = () => act(async () => {
    await request('/timetable/operations/confirm', { token, body: { preview_id: publishPreview.preview_id, confirmation_token: publishPreview.confirmation_token } });
    setRun({ ...run, status: 'PUBLISHED' }); setPublishPreview(null); setRefresh(r => r + 1);
  }, 'Timetable published with an immutable operation audit.');
  const generate = () => act(async () => {
    const preflight = await request(`/hod/timetable/terms/${term}/preflight`, { token, method: 'POST' });
    setReady(preflight);
    if (!preflight.ready) throw new Error('Resolve configuration issues before generation.');
    const editable = run && ['COMPLETE', 'PARTIAL', 'DRAFT'].includes(run.status);
    const result = await request(`/hod/timetable/terms/${term}/runs`, { token, body: { seed: Number(seed), ...(editable ? { parent_run_id: run.id } : {}) } });
    setRun(result); setSection(''); setRefresh(r => r + 1);
  }, 'Draft generated. Review validation and preview before publishing.');
  const entries = run ? draftEntries(run) : [];
  const editable = run && ['COMPLETE', 'PARTIAL', 'DRAFT'].includes(run.status);
  return <><PendingOperations /><BootstrapPanel term={term} onApplied={() => { setRefresh(r => r + 1); setReady(null); }} /><DepartmentConfig term={term} refresh={refresh} disabled={busy} /><section className="card p-4"><h2 className="font-bold">Configuration readiness</h2><div className="mt-3 flex flex-wrap items-end gap-3"><button className="btn-secondary" disabled={busy} onClick={() => act(async () => setReady(await request(`/hod/timetable/terms/${term}/preflight`, { token, method: 'POST' })), 'Readiness checked.')}>Check readiness</button><label className="text-sm">Seed<input className="field w-28" aria-label="Seed" type="number" min="0" max="2147483647" value={seed} disabled={busy} onChange={e => setSeed(e.target.value)} /></label><button className="btn-primary" disabled={busy} onClick={generate}>{busy ? 'Working…' : 'Generate Draft'}</button></div>{ready && <><p className="mt-3 font-semibold">{ready.ready ? `Ready · ${ready.required_periods} required periods` : 'Configuration needs attention'}</p><ul className="list-disc pl-5">{ready.issues.map((i, n) => <li key={n}>{i.message}</li>)}</ul></>}</section><Notice text={notice} />{busy && <p role="status" className="my-3">Processing timetable action. Generation may take up to 20 seconds.</p>}
    <section className="card my-4 p-4"><h2 className="font-bold">Run history</h2>{history.loading ? <LoadingSkeleton /> : history.error ? <ErrorState error={history.error} /> : !history.data.length ? <p>No drafts or published versions yet.</p> : <div className="mt-3 flex flex-wrap gap-2">{history.data.map(r => <button key={r.id} disabled={busy} className="btn-secondary" onClick={() => act(() => loadRun(r.id), 'Version loaded.')}>Version {r.id} · {r.status}</button>)}</div>}</section>
    {run && <><section className="card mb-4 p-4"><h2 className="text-lg font-bold">Version {run.id} · {run.status}</h2><p>{run.metrics.placed} / {run.metrics.required} periods · {run.metrics.steps} search steps · {run.metrics.search_conflicts} rejected candidates · {run.metrics.duration_ms} ms</p><h3 className="mt-3 font-bold">Hard conflicts ({run.conflicts.length})</h3>{run.conflicts.length ? <ul className="list-disc pl-5">{run.conflicts.map((i, n) => <li key={n}>{i.message}</li>)}</ul> : <p>No hard violations.</p>}<h3 className="mt-3 font-bold">Unplaced requirements</h3>{run.unplaced.length ? <ul className="list-disc pl-5">{run.unplaced.map(u => <li key={u.requirement_id}>{u.subject}: {u.missing_periods} missing periods. {u.reason}</li>)}</ul> : <p>All requirements placed.</p>}<div className="mt-4 flex flex-wrap gap-3"><button className="btn-secondary" disabled={busy || !editable} onClick={() => action('validate')}>Validate draft</button><button className="btn-primary" disabled={busy || run.status !== 'COMPLETE' || run.conflicts.length > 0 || publishPreview} onClick={previewPublish}>Preview publication</button></div>{publishPreview && <div className="mt-4 rounded-xl border border-amber-300 bg-amber-50 p-4" role="region" aria-label="Publication confirmation"><h3 className="font-bold">Publication preview</h3><p className="mt-1 text-sm">Version {publishPreview.summary.new_run_id} will become published. {publishPreview.summary.published_run_id ? `Version ${publishPreview.summary.published_run_id} will be archived.` : 'There is no current version to archive.'}</p><p className="mt-1 break-all text-xs text-muted">Correlation ID: {publishPreview.correlation_id}</p><div className="mt-3 flex flex-wrap gap-2"><button className="btn-primary" disabled={busy} onClick={confirmPublish}>Confirm publication</button><button className="btn-secondary" disabled={busy} onClick={() => setPublishPreview(null)}>Discard preview</button></div></div>}<p className="mt-2 text-sm text-muted">Publication always requires a preview and explicit confirmation. Previous published versions remain archived in history.</p></section><OccurrenceOperations run={run} /><label className="mb-3 block text-sm font-semibold">Preview section<select className="field max-w-md" value={section} onChange={e => setSection(e.target.value)}><option value="">All sections</option>{Object.entries(run.configuration.metadata.sections).map(([id, s]) => <option key={id} value={id}>{s.year}{s.name} / semester {s.semester}</option>)}</select></label><Weekly entries={entries.filter(e => !section || String(e.section_id) === section)} periodDefinitions={run.configuration.data.periods} busy={busy} lock={editable ? e => action(`entries/${e.id}/lock`, { locked: !e.locked }, 'PATCH') : null} /></>}
  </>;
}

export function AdminTimetable() {
  const { token } = useAuth();
  const [revision, setRevision] = useState(0);
  const [term, setTerm] = useState('');
  const [department, setDepartment] = useState('');
  const [tab, setTab] = useState('operations');
  const terms = useApi(() => request('/timetable/terms', { token }), [token, revision]);
  const config = useApi(() => request('/admin/timetable/configuration', { token }), [token, revision]);
  const overview = useApi(() => request('/principal/timetable/overview', { token }), [token, revision]);
  const save = path => async body => { await request(path, { token, body }); setRevision(n => n + 1); };
  if (terms.loading || config.loading || overview.loading) return <LoadingSkeleton />;
  if (terms.error || config.error || overview.error) return <ErrorState error={terms.error || config.error || overview.error} />;
  const departments = [...new Set(overview.data.map(item => item.department))];
  const selected = overview.data.find(item => String(item.term_id) === String(term) && item.department === department);
  const refreshOperations = () => setRevision(value => value + 1);
  return <><PageHeader title="Timetable operations" eyebrow="Administration / Draft, conflict review, publication and audit"><p className="mt-1 text-sm text-muted">Choose an academic term and department, then create only reviewed previews. Configuration remains available in its own section.</p></PageHeader><div className="mb-4 flex flex-wrap gap-2" role="tablist" aria-label="Timetable administration sections"><button role="tab" aria-selected={tab === 'operations'} className={tab === 'operations' ? 'btn-primary' : 'btn-secondary'} onClick={() => setTab('operations')}>Timetable operations</button><button role="tab" aria-selected={tab === 'configuration'} className={tab === 'configuration' ? 'btn-primary' : 'btn-secondary'} onClick={() => setTab('configuration')}>Academic configuration</button></div>{tab === 'operations' ? <><section className="card mb-4 p-4"><h2 className="font-bold">Select timetable scope</h2><div className="mt-3 grid gap-3 md:grid-cols-2"><TermPicker terms={terms.data} value={term} setValue={value => { setTerm(value); setDepartment(''); }} /><label className="block max-w-md text-sm font-semibold">Department<select aria-label="Timetable department" className="field mt-1" value={department} disabled={!term} onChange={event => setDepartment(event.target.value)}><option value="">Select a department</option>{departments.map(code => <option key={code} value={code}>{code}</option>)}</select></label></div>{!term || !department ? <p className="mt-2 text-sm text-muted">Select both a term and department to reveal generation, conflict, preview, publication, and history controls.</p> : null}</section>{selected ? <ScopedOperations item={selected} onChanged={refreshOperations} /> : term && department ? <p className="card p-4">No timetable scope exists for this term and department.</p> : null}<PendingOperations onChanged={refreshOperations} /></> : <><ConfigForm title="Academic term" fields={[{ name: 'name', label: 'Term name' }, { name: 'starts_on', label: 'Start date', type: 'date' }, { name: 'ends_on', label: 'End date', type: 'date' }]} save={save('/admin/timetable/terms')} /><ConfigForm title="Room" fields={[{ name: 'name', label: 'Unique room name' }, options('dept_code', 'Department', config.data.departments, d => d.name, false), { name: 'kind', label: 'Room type', defaultValue: 'classroom' }, number('capacity', 'Capacity', 60, 10000)]} save={save('/admin/timetable/rooms')} /><section className="card my-3 p-4"><h2 className="font-bold">Rooms</h2>{config.data.rooms.length ? config.data.rooms.map(r => <p key={r.id}>{r.name} · {r.dept_code} · {r.kind} · {r.capacity} seats</p>) : <p>No rooms configured.</p>}</section><TermPicker terms={terms.data} value={term} setValue={setTerm} />{term && <><PeriodEditor key={term} term={term} /><ConfigForm title="Holiday" fields={[{ name: 'date', label: 'Date', type: 'date' }, { name: 'label', label: 'Holiday name' }]} save={save(`/admin/timetable/terms/${term}/holidays`)} /></>}</>}</>;
}

function PeriodEditor({ term }) {
  const { token } = useAuth();
  const state = useApi(() => request(`/timetable/terms/${term}/periods`, { token }), [token, term]);
  const [periods, setPeriods] = useState([]);
  const { busy, notice, act } = useAction();
  useEffect(() => { if (state.data) setPeriods(state.data.periods.map(({ day_of_week, period_index, starts_at, ends_at, is_break, is_closed }) => ({ day_of_week, period_index, starts_at, ends_at, is_break, is_closed }))); }, [state.data]);
  if (state.loading) return <LoadingSkeleton />;
  if (state.error) return <ErrorState error={state.error} />;
  const update = (i, key, value) => setPeriods(old => old.map((p, n) => n === i ? { ...p, [key]: value } : p));
  return <section className="card p-4"><h2 className="font-bold">Weekly period template</h2><p className="my-2 text-sm text-muted">Use local Asia/Kolkata times. Period indices start at zero; omit closed weekdays. Consecutive lab periods must meet without a time gap.</p><div className="overflow-x-auto"><table className="min-w-[650px] w-full text-sm"><thead><tr><th>Day</th><th>Index</th><th>Start</th><th>End</th><th>Break</th><th>Closed</th></tr></thead><tbody>{periods.map((p, i) => <tr key={i}><td><select aria-label={`Day ${i+1}`} className="field" disabled={busy} value={p.day_of_week} onChange={e => update(i, 'day_of_week', Number(e.target.value))}>{days.map((d, n) => <option key={d} value={n}>{d}</option>)}</select></td><td><input className="field" aria-label={`Index ${i+1}`} type="number" min="0" max="23" disabled={busy} value={p.period_index} onChange={e => update(i, 'period_index', Number(e.target.value))} /></td>{['starts_at', 'ends_at'].map(k => <td key={k}><input className="field" aria-label={`${k} ${i+1}`} type="time" disabled={busy} value={p[k]} onChange={e => update(i, k, e.target.value)} /></td>)}{['is_break', 'is_closed'].map(k => <td className="text-center" key={k}><input aria-label={`${k} ${i+1}`} type="checkbox" disabled={busy} checked={p[k]} onChange={e => update(i, k, e.target.checked)} /></td>)}</tr>)}</tbody></table></div><div className="mt-3 flex flex-wrap gap-3"><button className="btn-secondary" disabled={busy} onClick={() => setPeriods(old => [...old, { day_of_week: 0, period_index: old.filter(p => p.day_of_week === 0).length, starts_at: '09:00', ends_at: '10:00', is_break: false, is_closed: false }])}>Add period</button><button className="btn-primary" disabled={busy || !periods.length} onClick={() => act(() => request(`/admin/timetable/terms/${term}/periods`, { token, method: 'PUT', body: { periods } }))}>{busy ? 'Saving…' : 'Save period template'}</button></div><Notice text={notice} />{state.data.holidays.map(h => <p className="mt-2" key={h.id}>{h.date} · {h.label}</p>)}</section>;
}

export function PrincipalTimetable() {
  const { token } = useAuth();
  const { data, loading, error } = useApi(() => request('/principal/timetable/overview', { token }), [token]);
  if (loading) return <LoadingSkeleton />;
  if (error) return <ErrorState error={error} />;
  return <><PageHeader title="Timetable operations" eyebrow="Institution / Generation, publication and audit" /><PendingOperations />{data.length ? <div className="grid min-w-0 gap-4 md:grid-cols-2">{data.map(r => <InstitutionActionCard item={r} key={`${r.term_id}-${r.department}`} />)}</div> : <p className="card p-4">No academic terms configured.</p>}</>;
}
