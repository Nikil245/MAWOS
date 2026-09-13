import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { BookOpen } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { api } from '../../services/api';
import { DashboardCard, EmptyState, ErrorState, LoadingSkeleton, PageHeader, StatCard, StatusBadge } from '../../components/ui';

const date = value => value ? new Date(value).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', dateStyle: 'medium', timeStyle: 'short' }) + ' IST' : '—';
const money = value => `₹${Number(value || 0).toFixed(2)}`;
const PAGE = 20;
function useLibrary(path) {
  const { token } = useAuth();
  const [data, setData] = useState(null), [error, setError] = useState(null), [loading, setLoading] = useState(true), [revision, setRevision] = useState(0);
  useEffect(() => {
    const refresh = () => setRevision(value => value + 1);
    window.addEventListener('mawos:library-changed', refresh);
    return () => window.removeEventListener('mawos:library-changed', refresh);
  }, []);
  useEffect(() => {
    let current = true; setLoading(true); setError(null); setData(null);
    api.libraryRequest(token, path).then(value => { if (current) setData(value); }).catch(reason => { if (current) setError(reason); }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [path, token, revision]);
  return { data, error, loading, reload: () => setRevision(value => value + 1) };
}
function Load({ state, children }) {
  return state.loading ? <div role="status" aria-label="Loading library data"><LoadingSkeleton rows={3} /></div> : state.error ? <ErrorState error={state.error} retry={state.reload} /> : children;
}
function Pager({ offset, total, setOffset }) {
  return <div className="mt-4 flex flex-wrap items-center gap-3 text-sm"><button className="btn-secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button><span>{total ? `${offset + 1}–${Math.min(offset + PAGE, total)} of ${total}` : '0 results'}</span><button className="btn-secondary" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next</button></div>;
}
function useAction(reload) {
  const { token } = useAuth(); const [busy, setBusy] = useState(false), [error, setError] = useState(null), [message, setMessage] = useState('');
  const run = async (path, body = {}, method = 'POST') => {
    setBusy(true); setError(null); setMessage('');
    try { const result = await api.libraryRequest(token, path, body, method); reload?.(); setMessage('Library updated.'); window.dispatchEvent(new Event('mawos:notifications-changed')); window.dispatchEvent(new Event('mawos:library-changed')); return result; }
    catch (reason) { setError(reason); return null; }
    finally { setBusy(false); }
  };
  return { run, busy, error, message };
}
function ActionStatus({ action }) {
  return <>{action.error && <div role="alert" className="my-3 rounded-lg bg-red-50 p-3 text-sm text-red-700">{action.error.message}</div>}{action.message && <p role="status" className="my-3 text-sm text-green-700">{action.message}</p>}</>;
}

export function LibrarySummaryCard({ data, parent = false }) {
  return <DashboardCard title="Library" action={!parent && <Link className="text-primary text-sm font-semibold" to="/student/library">Open Library</Link>}>
    <div id={parent ? 'library' : undefined}><BookOpen className="text-primary" size={22} />
      {!data ? <p className="mt-3 text-sm text-muted">Library summary unavailable.</p> : <>
        <p className="mt-3 font-semibold">{data.issued_count} issued books · {money(data.unpaid_total)} unpaid fines</p>
        <p className="mt-2 text-sm">Current estimated overdue amount: {money(data.estimated_overdue_total)}</p>
        <p className={`mt-1 text-sm ${data.next_due_at && new Date(data.next_due_at) < new Date() ? 'text-red-700' : 'text-muted'}`}>Next due: {date(data.next_due_at)}</p>
        {parent && <><p className="mt-2 text-sm">Paid fines: {money(data.paid_total)}</p>{data.borrowed?.length ? data.borrowed.map((row, index) => <article className="mt-3 border-t pt-3 text-sm" key={index}><b>{row.title}</b><p>Due {date(row.due_at)} · {row.status.replaceAll('_', ' ')}</p><p>Estimated overdue: {money(row.estimated_fine)}</p></article>) : <p className="mt-3 text-sm text-muted">No borrowed books.</p>}</>}
      </>}
    </div>
  </DashboardCard>;
}

function BookDetail({ id, close }) {
  const book = useLibrary(`/library/books/${id}`), [offset, setOffset] = useState(0);
  const reviews = useLibrary(`/library/books/${id}/reviews?offset=${offset}&limit=${PAGE}`);
  return <section role="dialog" aria-modal="true" aria-label="Book details and reviews" className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onKeyDown={event => { if (event.key === 'Escape') close(); }}>
    <div className="max-h-[90vh] w-full max-w-2xl overflow-auto rounded-2xl bg-white p-6 shadow-xl"><button autoFocus className="btn-secondary float-right" onClick={close}>Close details</button><h2 className="text-xl font-bold">Book details</h2>
      <Load state={book}>{book.data && <><h3 className="mt-5 text-lg font-semibold">{book.data.title}</h3><p>{book.data.author} · {book.data.isbn}</p><p className="mt-3 whitespace-pre-wrap">{book.data.description || 'No description available.'}</p><p className="mt-3">{book.data.available_copies} available · Average rating: {book.data.average_rating ?? 'Not yet rated'}</p></>}</Load>
      <h3 className="mt-6 font-bold">Reader reviews</h3><Load state={reviews}>{reviews.data?.items.length ? reviews.data.items.map(row => <article className="mt-3 border-t pt-3" key={row.id}><b>{row.rating}/5</b><p className="whitespace-pre-wrap break-words text-sm">{row.comment || 'Rating only'}</p></article>) : <EmptyState title="No reviews yet" />}</Load><Pager offset={offset} total={reviews.data?.total || 0} setOffset={setOffset} />
    </div>
  </section>;
}

const blankBook = { isbn: '', title: '', author: '', publisher: '', category: '', description: '', departments: '', total_copies: 1, is_active: true };
function BookEditor({ book, save, busy, close }) {
  const [form, setForm] = useState(book ? { ...book, departments: book.departments.join(', ') } : blankBook);
  const submit = event => { event.preventDefault(); save({ isbn: form.isbn, title: form.title, author: form.author, publisher: form.publisher || null, category: form.category, description: form.description || null, departments: form.departments.split(',').map(value => value.trim()).filter(Boolean), total_copies: Number(form.total_copies), is_active: form.is_active }); };
  return <form onSubmit={submit} className="mb-4 rounded-xl border bg-slate-50 p-4"><h3 className="font-bold">{book ? 'Edit book' : 'Add book'}</h3><div className="mt-3 grid gap-3 sm:grid-cols-2">{['isbn', 'title', 'author', 'publisher', 'category', 'departments'].map(key => <label className="label capitalize" key={key}>{key === 'departments' ? 'Department codes (comma separated, blank for all)' : key}<input className="field" required={['isbn', 'title', 'author', 'category'].includes(key)} value={form[key] || ''} maxLength={key === 'isbn' ? 32 : 256} onChange={event => setForm({ ...form, [key]: event.target.value })} /></label>)}
    <label className="label">Total copies<input className="field" type="number" min="0" max="100000" required value={form.total_copies} onChange={event => setForm({ ...form, total_copies: event.target.value })} /></label><label className="label">Description<textarea className="field" maxLength="10000" value={form.description || ''} onChange={event => setForm({ ...form, description: event.target.value })} /></label>
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.is_active} onChange={event => setForm({ ...form, is_active: event.target.checked })} />Active catalogue entry</label></div><p className="mt-3 text-xs text-muted">Availability is calculated from total stock and copies currently held or issued.</p><div className="mt-3 flex gap-2"><button className="btn-primary" disabled={busy}>Save book</button><button type="button" className="btn-secondary" onClick={close}>Cancel edit</button></div></form>;
}

function Catalogue({ staff = false, onChange }) {
  const [searchParams] = useSearchParams();
  const initialQuery = (searchParams.get('q') || '').slice(0, 128);
  const [search, setSearch] = useState(initialQuery), [query, setQuery] = useState(initialQuery), [offset, setOffset] = useState(0), [detail, setDetail] = useState(null), [editing, setEditing] = useState(null);
  const state = useLibrary(`/library/books?q=${encodeURIComponent(query)}&offset=${offset}&limit=${PAGE}&include_archived=${staff}`);
  const navigate = useNavigate(); const action = useAction(() => { state.reload(); onChange?.(); });
  const reserve = async id => { const result = await action.run('/student/library/reservations', { book_id: id }); if (result) navigate(`/student/library/slips/${result.id}`); };
  const save = async body => { const result = await action.run(`/librarian/library/books${editing.id ? `/${editing.id}` : ''}`, body, editing.id ? 'PUT' : 'POST'); if (result) setEditing(null); };
  return <DashboardCard title="Catalogue" action={staff && <button className="btn-secondary" onClick={() => setEditing({})}>Add book</button>}>
    <form onSubmit={event => { event.preventDefault(); setQuery(search.trim()); setOffset(0); }} className="mb-4 flex gap-2"><input aria-label="Search catalogue" className="field min-w-0" placeholder="Title, author, ISBN or category" maxLength="128" value={search} onChange={event => setSearch(event.target.value)} /><button className="btn-primary">Search</button></form>
    <ActionStatus action={action} />{editing && <BookEditor key={editing.id || 'new'} book={editing.id ? editing : null} busy={action.busy} save={save} close={() => setEditing(null)} />}
    <Load state={state}>{state.data?.items.length ? <div className="grid gap-3 md:grid-cols-2">{state.data.items.map(book => <article className="rounded-xl border p-4" key={book.id}><div className="flex items-start justify-between gap-2"><h3 className="font-bold break-words">{book.title}</h3><StatusBadge>{book.is_active ? `${book.available_copies} available` : 'Archived'}</StatusBadge></div><p className="mt-1 text-sm text-muted">{book.author} · {book.category}</p><p className="mt-1 text-xs text-muted">Book #{book.id} · ISBN {book.isbn} · Rating {book.average_rating ?? '—'}</p><div className="mt-4 flex flex-wrap gap-2"><button className="btn-secondary" onClick={() => setDetail(book.id)}>Details & reviews</button>{staff ? <><button className="btn-secondary" onClick={() => setEditing(book)}>Edit</button>{book.is_active && <button className="btn-secondary" disabled={action.busy} onClick={() => action.run(`/librarian/library/books/${book.id}/archive`)}>Archive</button>}</> : <button className="btn-primary" disabled={action.busy || !book.is_active || book.available_copies < 1} onClick={() => reserve(book.id)}>Reserve</button>}</div></article>)}</div> : <EmptyState title="No books found" detail="Try another title, author, ISBN or category." />}</Load>
    <Pager offset={offset} total={state.data?.total || 0} setOffset={setOffset} />{detail && <BookDetail key={detail} id={detail} close={() => setDetail(null)} />}
  </DashboardCard>;
}

function ReturnRequest({ row, action, close }) {
  const [rating, setRating] = useState(''), [comment, setComment] = useState('');
  return <form className="mt-3 rounded-lg bg-slate-50 p-3" onSubmit={async event => { event.preventDefault(); const result = await action.run(`/student/library/issues/${row.id}/return-request`, { rating: rating ? Number(rating) : null, comment: comment || null }); if (result) close(); }}>
    <p className="text-sm">Hand the book to the librarian. Fines continue until physical return is confirmed.</p><label className="label mt-3">Rating (optional)<select className="field" value={rating} onChange={event => setRating(event.target.value)}><option value="">No rating</option>{[1, 2, 3, 4, 5].map(value => <option key={value} value={value}>{value}/5</option>)}</select></label><label className="label mt-3">Review (optional)<textarea className="field" maxLength="2000" value={comment} onChange={event => setComment(event.target.value)} /></label><div className="mt-3 flex gap-2"><button className="btn-primary" disabled={action.busy || Boolean(comment && !rating)}>Submit return request</button><button className="btn-secondary" type="button" onClick={close}>Cancel request form</button></div>
  </form>;
}

function StudentRecords({ onChange }) {
  const [kind, setKind] = useState('reservations'), [offset, setOffset] = useState(0), [returnId, setReturnId] = useState(null);
  const state = useLibrary(`/student/library/${kind}?offset=${offset}&limit=${PAGE}`);
  const action = useAction(() => { state.reload(); onChange(); });
  return <DashboardCard title="My library records"><div className="mb-4 flex flex-wrap gap-2">{[['reservations', 'My reservations'], ['issues', 'Borrowed books & history'], ['fines', 'Fine history']].map(([key, label]) => <button className={kind === key ? 'btn-primary' : 'btn-secondary'} key={key} onClick={() => { setKind(key); setOffset(0); setReturnId(null); }}>{label}</button>)}</div><ActionStatus action={action} />
    <Load state={state}>{state.data?.items.length ? <div className="space-y-3">{state.data.items.map(row => <article className="rounded-xl border p-4" key={row.id}><div className="flex flex-wrap justify-between gap-2"><h3 className="font-bold">{row.title || row.reason}</h3><StatusBadge>{row.status.replaceAll('_', ' ')}</StatusBadge></div>
      {kind === 'reservations' && <><p className="mt-2 text-sm">Pickup deadline: {date(row.pickup_deadline)}</p><div className="mt-3 flex gap-2"><Link className="btn-secondary" to={`/student/library/slips/${row.id}`}>View slip</Link>{row.status === 'PENDING_PICKUP' && <button className="btn-secondary" disabled={action.busy} onClick={() => action.run(`/student/library/reservations/${row.id}/cancel`)}>Cancel reservation</button>}</div></>}
      {kind === 'issues' && <><p className="mt-2 text-sm">Due: {date(row.due_at)} · Current estimated fine: {money(row.estimated_fine)}</p>{row.returned_at && <p className="text-sm">Returned {date(row.returned_at)}</p>}{row.return_rejection_reason && <p role="status" className="mt-2 text-sm text-red-700">Return rejected: {row.return_rejection_reason}</p>}{row.status === 'RETURN_PENDING' && <p className="mt-2 text-sm text-muted">Awaiting physical return confirmation. Fines continue to accrue.</p>}{row.status === 'ISSUED' && <button className="btn-secondary mt-3" onClick={() => setReturnId(row.id)}>Request return</button>}{returnId === row.id && <ReturnRequest row={row} action={action} close={() => setReturnId(null)} />}</>}
      {kind === 'fines' && <><p className="mt-2 font-semibold">{money(row.amount)}</p><p className="text-sm">Created {date(row.created_at)}{row.paid_at && ` · Paid ${date(row.paid_at)}`}</p><p className="mt-2 text-sm text-muted">{row.status === 'UNPAID' ? 'Pay in person at the library counter.' : 'Payment recorded by the library.'}</p></>}
    </article>)}</div> : <EmptyState title="No library records yet" />}</Load><Pager offset={offset} total={state.data?.total || 0} setOffset={setOffset} /></DashboardCard>;
}
function Recommendations() {
  const state = useLibrary('/student/library/recommendations'); const [detail, setDetail] = useState(null);
  return <DashboardCard title="Recommended for you"><Load state={state}>{state.data?.items.length ? <div className="space-y-3">{state.data.items.map(row => <button className="block w-full rounded-lg border p-3 text-left hover:bg-slate-50" key={row.id} onClick={() => setDetail(row.id)}><b>{row.title}</b><p className="mt-1 text-xs text-muted">{row.explanations.join(' · ')}</p></button>)}</div> : <EmptyState title="No recommendations yet" />}</Load>{detail && <BookDetail key={detail} id={detail} close={() => setDetail(null)} />}</DashboardCard>;
}
export function StudentLibrary() {
  const state = useLibrary('/student/library/summary'); const [revision, setRevision] = useState(0);
  const reload = () => { state.reload(); setRevision(value => value + 1); };
  return <><PageHeader title="My Library" eyebrow="Student / Physical library" actions={<button className="btn-secondary" onClick={() => window.dispatchEvent(new Event('mawos:library-changed'))}>Refresh Library</button>} /><p className="mb-5 text-sm text-muted">Reserve a copy, show your slip at pickup, and hand returns to the librarian. Fines are collected in person.</p><div className="mb-5"><Load state={state}><LibrarySummaryCard data={state.data} /></Load></div><div className="space-y-5"><Catalogue onChange={reload} /><StudentRecords onChange={reload} /><Recommendations key={revision} /></div></>;
}
export function LibrarySlip() {
  const { reservationId } = useParams(), { token } = useAuth();
  const state = useLibrary(`/student/library/reservations/${reservationId}/slip`); const [error, setError] = useState(null), [busy, setBusy] = useState(false);
  const download = async () => {
    setBusy(true); setError(null);
    try { await api.downloadLibrarySlip(token, reservationId); } catch (reason) { setError(reason); } finally { setBusy(false); }
  };
  return <><PageHeader title="Pickup acknowledgement" eyebrow="Student / Private reservation slip" actions={<Link className="btn-secondary" to="/student/library">Back to Library</Link>} /><Load state={state}>{state.data && <DashboardCard title={state.data.title}><p>{state.data.author}</p><p className="mt-3">{state.data.student_name} · {state.data.student_usn}</p><p className="mt-6 text-sm text-muted">Your pickup code</p><p className="my-3 font-mono text-4xl font-bold tracking-widest">{state.data.slip_code}</p><StatusBadge>{state.data.status.replaceAll('_', ' ')}</StatusBadge><p className="mt-4">Collect by {date(state.data.pickup_deadline)}</p><p className="mt-2 text-sm text-muted">Bring this slip and your student ID to the library. The librarian checks validity at handover.</p><button className="btn-primary mt-5" disabled={busy} onClick={download}>Download PDF slip</button>{error && <p role="alert" className="mt-3 text-red-700">{error.message}</p>}</DashboardCard>}</Load></>;
}

function StaffRecords({ onChange }) {
  const [kind, setKind] = useState('pickups'), [offset, setOffset] = useState(0), [rejectId, setRejectId] = useState(null), [reason, setReason] = useState('');
  const state = useLibrary(`/librarian/library/records/${kind}?offset=${offset}&limit=${PAGE}`);
  const action = useAction(() => { state.reload(); onChange(); });
  return <DashboardCard title="Library operations"><div className="mb-4 flex flex-wrap gap-2">{[['pickups', 'Pending pickups'], ['returns', 'Pending returns'], ['issues', 'All issues / Counter returns'], ['overdue', 'Overdue report'], ['fines', 'Fine collection'], ['reviews', 'Reviews']].map(([key, label]) => <button key={key} className={kind === key ? 'btn-primary' : 'btn-secondary'} onClick={() => { setKind(key); setOffset(0); setRejectId(null); }}>{label}</button>)}</div><ActionStatus action={action} />
    <Load state={state}>{state.data?.items.length ? <div className="space-y-3">{state.data.items.map(row => <article className="rounded-xl border p-4" key={row.id}><div className="flex flex-wrap justify-between gap-2"><b>{row.title || row.reason || `Book #${row.book_id}`}</b><StatusBadge>{row.status?.replaceAll('_', ' ') || `${row.rating}/5`}</StatusBadge></div><p className="mt-1 text-sm text-muted">{row.student_usn} · Record #{row.id}</p>
      {kind === 'pickups' && <><p className="mt-2 text-sm">Pickup deadline: {date(row.pickup_deadline)}</p><button className="btn-primary mt-3" disabled={action.busy || new Date(row.pickup_deadline) <= new Date()} onClick={() => action.run(`/librarian/library/reservations/${row.id}/pickup`)}>Confirm physical pickup</button></>}
      {['issues', 'returns', 'overdue'].includes(kind) && <><p className="mt-2 text-sm">Due {date(row.due_at)} · Estimated fine {money(row.estimated_fine)}</p>{row.status !== 'RETURNED' && <div className="mt-3 flex flex-wrap gap-2"><button className="btn-primary" disabled={action.busy} onClick={() => action.run(`/librarian/library/issues/${row.id}/return`)}>Confirm physical return</button>{row.status === 'RETURN_PENDING' && <button className="btn-secondary" onClick={() => { setRejectId(row.id); setReason(''); }}>Reject return</button>}</div>}{rejectId === row.id && <form className="mt-3 flex flex-wrap gap-2" onSubmit={async event => { event.preventDefault(); if (await action.run(`/librarian/library/issues/${row.id}/reject-return`, { reason })) setRejectId(null); }}><input aria-label="Return rejection reason" className="field" required maxLength="1000" value={reason} onChange={event => setReason(event.target.value)} /><button className="btn-primary" disabled={action.busy || !reason.trim()}>Save rejection</button></form>}</>}
      {kind === 'fines' && <><p className="mt-2 font-semibold">{money(row.amount)} · {date(row.created_at)}</p>{row.status === 'UNPAID' && <button className="btn-primary mt-3" disabled={action.busy} onClick={() => action.run(`/librarian/library/fines/${row.id}/paid`)}>Confirm cash collected / Mark paid</button>}{row.paid_at && <p className="text-sm">Paid {date(row.paid_at)}</p>}</>}
      {kind === 'reviews' && <p className="mt-2 whitespace-pre-wrap break-words text-sm">{row.comment || 'Rating only'}</p>}
    </article>)}</div> : <EmptyState title="No matching library records" />}</Load><Pager offset={offset} total={state.data?.total || 0} setOffset={setOffset} /></DashboardCard>;
}
export function LibrarianLibrary() {
  const summary = useLibrary('/librarian/library/summary'); const [revision, setRevision] = useState(0), [code, setCode] = useState(''), [verified, setVerified] = useState(null), [usn, setUsn] = useState(''), [bookId, setBookId] = useState('');
  const { token, user } = useAuth(); const [verifyError, setVerifyError] = useState(null), [verifying, setVerifying] = useState(false);
  const reload = () => { summary.reload(); setRevision(value => value + 1); };
  const action = useAction(reload);
  const verify = async event => { event.preventDefault(); setVerified(null); setVerifyError(null); setVerifying(true); try { setVerified(await api.libraryRequest(token, '/librarian/library/verify-slip', { slip_code: code }, 'POST')); } catch (reason) { setVerifyError(reason); } finally { setVerifying(false); } };
  return <><PageHeader title="Library desk" eyebrow={`${user?.role === 'admin' ? 'Admin oversight' : 'Librarian'} / Physical lending`} actions={<button className="btn-secondary" onClick={() => window.dispatchEvent(new Event('mawos:library-changed'))}>Refresh Library</button>} /><Load state={summary}>{summary.data && <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">{[['Issued books', summary.data.issued_count], ['Pending pickups', summary.data.pending_pickups], ['Pending returns', summary.data.pending_returns], ['Overdue books', summary.data.overdue_count], ['Unpaid fines', money(summary.data.unpaid_total)]].map(([label, value]) => <StatCard key={label} label={label} value={value} icon={BookOpen} />)}</div>}</Load><ActionStatus action={action} />
    <div className="mb-5 grid gap-5 lg:grid-cols-2"><DashboardCard title="Verify pickup slip"><form onSubmit={verify} className="flex gap-2"><input aria-label="Six-digit slip code" className="field min-w-0" inputMode="numeric" pattern="[0-9]{6}" maxLength="6" required value={code} onChange={event => { setCode(event.target.value); setVerified(null); }} /><button className="btn-primary" disabled={verifying}>Verify slip</button></form>{verifyError && <p role="alert" className="mt-3 text-red-700">{verifyError.message}</p>}{verified && <div className="mt-4 rounded-lg border p-3"><b>{verified.title}</b><p>{verified.student_name} · {verified.student_usn}</p><p className="text-sm">Collect by {date(verified.pickup_deadline)}</p><button className="btn-primary mt-3" disabled={action.busy} onClick={async () => { if (await action.run(`/librarian/library/reservations/${verified.id}/pickup`)) { setVerified(null); setCode(''); } }}>Confirm verified pickup</button></div>}</DashboardCard>
    <DashboardCard title="Direct counter issue"><form onSubmit={async event => { event.preventDefault(); if (await action.run('/librarian/library/issues', { student_usn: usn, book_id: Number(bookId) })) { setUsn(''); setBookId(''); } }}><div className="grid gap-3 sm:grid-cols-2"><label className="label">Student USN<input className="field" maxLength="16" required value={usn} onChange={event => setUsn(event.target.value.toUpperCase())} /></label><label className="label">Book ID<input className="field" type="number" min="1" required value={bookId} onChange={event => setBookId(event.target.value)} /></label></div><button className="btn-primary mt-3" disabled={action.busy}>Issue at counter</button></form><p className="mt-3 text-xs text-muted">Confirm only when handing the physical book to the student.</p></DashboardCard></div>
    <div className="space-y-5"><StaffRecords key={`records-${revision}`} onChange={summary.reload} /><Catalogue key={`catalogue-${revision}`} staff onChange={summary.reload} /></div></>;
}

export function LibrarianManagement() {
  const [offset, setOffset] = useState(0), [username, setUsername] = useState(''), [name, setName] = useState(''), [credentials, setCredentials] = useState(null), [editing, setEditing] = useState(null);
  const state = useLibrary(`/admin/librarians?offset=${offset}&limit=${PAGE}`), action = useAction(state.reload);
  return <><PageHeader title="Librarian management" eyebrow="Admin / Library accounts" actions={<Link className="btn-secondary" to="/admin/library">Library oversight</Link>} /><ActionStatus action={action} />
    {credentials && <section role="status" className="mb-5 rounded-xl border border-amber-300 bg-amber-50 p-5"><h2 className="font-bold">Save these temporary credentials now</h2><p className="mt-2 font-mono break-all">{credentials.username} · {credentials.temporary_password}</p><p className="mt-2 text-sm">Shown once. The librarian must change the password at first login.</p><button className="btn-secondary mt-3" onClick={() => setCredentials(null)}>I have saved them</button></section>}
    <div className="grid gap-5 lg:grid-cols-2"><DashboardCard title="Create librarian"><form onSubmit={async event => { event.preventDefault(); const result = await action.run('/admin/librarians', { username, display_name: name }); if (result) { setCredentials(result.generated_credentials); setUsername(''); setName(''); } }}><label className="label">Username<input className="field" required minLength="3" maxLength="64" pattern="[a-z0-9._@-]+" value={username} onChange={event => setUsername(event.target.value.toLowerCase())} /></label><label className="label mt-3">Display name<input className="field" required minLength="2" maxLength="128" value={name} onChange={event => setName(event.target.value)} /></label><button className="btn-primary mt-4" disabled={action.busy}>Create librarian account</button></form></DashboardCard>
    <DashboardCard title="Existing librarians"><Load state={state}>{state.data?.items.length ? state.data.items.map(row => <article className="mb-3 rounded-xl border p-4" key={row.id}><b>{row.display_name}</b><p className="text-sm">{row.username} · {row.active ? 'Active' : 'Inactive'}{row.must_change_password ? ' · Password change required' : ''}</p><div className="mt-3 flex gap-2"><button className="btn-secondary" onClick={() => setEditing({ ...row })}>Edit name</button><button className="btn-secondary" disabled={action.busy} onClick={() => action.run(`/admin/librarians/${row.id}`, { display_name: row.display_name, active: !row.active }, 'PUT')}>{row.active ? 'Deactivate' : 'Activate'}</button></div>{editing?.id === row.id && <form className="mt-3" onSubmit={async event => { event.preventDefault(); if (await action.run(`/admin/librarians/${row.id}`, { display_name: editing.display_name, active: row.active }, 'PUT')) setEditing(null); }}><input aria-label="Edit librarian name" className="field" required minLength="2" maxLength="128" value={editing.display_name} onChange={event => setEditing({ ...editing, display_name: event.target.value })} /><button className="btn-primary mt-2" disabled={action.busy}>Save name</button></form>}</article>) : <EmptyState title="No librarian accounts" />}</Load><Pager offset={offset} total={state.data?.total || 0} setOffset={setOffset} /></DashboardCard></div></>;
}
