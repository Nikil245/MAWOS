import { useState } from 'react';
import { CalendarDays, Clock3, MapPin } from 'lucide-react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { useApi } from '../../hooks/useApi';
import { api } from '../../services/api';
import { DashboardCard, DataTable, EmptyState, ErrorState, LoadingSkeleton, PageHeader, StatusBadge, Toast } from '../../components/ui';

function dateLabel(value) {
  return new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeZone: 'Asia/Kolkata' })
    .format(new Date(`${value}T00:00:00+05:30`));
}

function timeLabel(event) {
  if (!event.start_time) return null;
  const start = event.start_time.slice(0, 5);
  return event.end_time ? `${start}–${event.end_time.slice(0, 5)}` : start;
}

function EventSummary({ event, showDate = false }) {
  return <Link to={`/events/${event.id}`} className="block rounded-lg border p-3 hover:border-primary hover:bg-blue-50">
    <p className="text-sm font-semibold">{event.title}</p>
    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
      {showDate && <span className="inline-flex items-center gap-1"><CalendarDays size={13} />{dateLabel(event.event_date)}</span>}
      {timeLabel(event) && <span className="inline-flex items-center gap-1"><Clock3 size={13} />{timeLabel(event)}</span>}
      {event.venue && <span className="inline-flex items-center gap-1"><MapPin size={13} />{event.venue}</span>}
    </div>
    {event.description && <p className="mt-2 line-clamp-2 text-xs text-muted">{event.description}</p>}
  </Link>;
}

export function CampusEventsCard({ className = '' }) {
  const { token } = useAuth();
  const [revision, setRevision] = useState(0);
  const { data, loading, error } = useApi(() => api.campusEvents(token), [token, revision]);
  return <DashboardCard title="Campus events" className={className} action={<Link className="text-sm font-semibold text-primary" to="/events">View all events</Link>}>
    {loading ? <LoadingSkeleton rows={3} /> : error ? <ErrorState error={error} retry={() => setRevision(value => value + 1)} /> : <div className="grid gap-4 sm:grid-cols-2">
      <section><h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-muted">Today</h3>
        {data.today.length ? <div className="space-y-2">{data.today.map(event => <EventSummary key={event.id} event={event} />)}</div> : <p className="text-sm text-muted">No events scheduled today.</p>}
      </section>
      <section><h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-muted">Upcoming</h3>
        {data.upcoming.length ? <div className="max-h-64 space-y-2 overflow-y-auto">{data.upcoming.map(event => <EventSummary key={event.id} event={event} showDate />)}</div> : <p className="text-sm text-muted">No upcoming events.</p>}
      </section>
    </div>}
  </DashboardCard>;
}

export function EventsList() {
  const { token } = useAuth();
  const { data, loading, error } = useApi(() => api.campusEvents(token), [token]);
  if (loading) return <LoadingSkeleton rows={6} />;
  if (error) return <ErrorState error={error} />;
  return <><PageHeader title="Campus events" eyebrow="Published announcements" />
    {data.events.length ? <div className="grid gap-3 md:grid-cols-2">{data.events.map(event => <EventSummary key={event.id} event={event} showDate />)}</div>
      : <EmptyState title="No current campus events" detail="Published events will appear here." />}</>;
}

export function EventDetail() {
  const { token } = useAuth();
  const { eventId } = useParams();
  const { data, loading, error } = useApi(() => api.campusEvent(token, eventId), [token, eventId]);
  if (loading) return <LoadingSkeleton rows={4} />;
  if (error) return <ErrorState error={error} />;
  return <><PageHeader title={data.title} eyebrow={dateLabel(data.event_date)} />
    <section className="card max-w-3xl p-5">
      <div className="flex flex-wrap gap-3 text-sm text-muted">
        {timeLabel(data) && <span className="inline-flex items-center gap-1"><Clock3 size={16} />{timeLabel(data)}</span>}
        {data.venue && <span className="inline-flex items-center gap-1"><MapPin size={16} />{data.venue}</span>}
      </div>
      {data.organizer && <p className="mt-4 text-sm"><b>Organizer:</b> {data.organizer}</p>}
      {data.description && <p className="mt-4 whitespace-pre-wrap text-sm text-slate-700">{data.description}</p>}
      <Link className="btn-secondary mt-6 inline-flex" to="/events">Back to all events</Link>
    </section></>;
}

const blank = {
  title: '', description: '', event_date: '', start_time: '', end_time: '',
  venue: '', organizer: '', audience: 'ALL', department_code: '', status: 'DRAFT',
};

function inputValue(value) {
  return value ?? '';
}

export function AdminCampusEvents() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const [revision, setRevision] = useState(0);
  const [form, setForm] = useState(blank);
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState('');
  const { data, loading, error } = useApi(() => api.adminCampusEvents(token), [token, revision]);
  const update = event => setForm(current => ({ ...current, [event.target.name]: event.target.value }));
  const beginEdit = event => {
    setEditing(event.id);
    setForm({ ...blank, ...event, department_code: inputValue(event.department_code),
      description: inputValue(event.description), venue: inputValue(event.venue),
      organizer: inputValue(event.organizer), start_time: inputValue(event.start_time)?.slice(0, 5),
      end_time: inputValue(event.end_time)?.slice(0, 5) });
  };
  const submit = async event => {
    event.preventDefault(); setBusy(true); setToast('');
    try {
      const body = { ...form, department_code: form.department_code || null,
        start_time: form.start_time || null, end_time: form.end_time || null };
      if (editing) delete body.status;
      await api.saveCampusEvent(token, editing, body);
      setToast(editing ? 'Campus event updated.' : 'Campus event created.');
      setForm(blank); setEditing(null); setRevision(value => value + 1);
      window.dispatchEvent(new Event('mawos:notifications-changed'));
    } catch (requestError) { setToast(requestError.message); } finally { setBusy(false); }
  };
  const action = async (id, name) => {
    setBusy(true); setToast('');
    try {
      const body = name === 'cancel' ? { reason: window.prompt('Cancellation reason (optional)') || null } : {};
      await api.campusEventAction(token, id, name, body);
      setToast(name === 'publish' ? 'Campus event published.' : 'Campus event cancelled.');
      setRevision(value => value + 1);
      window.dispatchEvent(new Event('mawos:notifications-changed'));
    } catch (requestError) { setToast(requestError.message); } finally { setBusy(false); }
  };
  return <><PageHeader title="Campus events" eyebrow="Admin / Announcement calendar" actions={<button className="btn-secondary" onClick={() => navigate('/events')}>View published events</button>} />
    <form className="card mb-5 grid gap-3 p-5 sm:grid-cols-2 xl:grid-cols-4" onSubmit={submit}>
      <label className="text-sm font-medium sm:col-span-2">Title<input className="field mt-1" name="title" required maxLength="200" value={form.title} onChange={update} /></label>
      <label className="text-sm font-medium">Event date<input className="field mt-1" name="event_date" type="date" required value={form.event_date} onChange={update} /></label>
      {!editing && <label className="text-sm font-medium">Initial status<select className="field mt-1" name="status" value={form.status} onChange={update}><option>DRAFT</option><option>PUBLISHED</option></select></label>}
      <label className="text-sm font-medium">Start time<input className="field mt-1" name="start_time" type="time" value={form.start_time} onChange={update} /></label>
      <label className="text-sm font-medium">End time<input className="field mt-1" name="end_time" type="time" value={form.end_time} onChange={update} /></label>
      <label className="text-sm font-medium">Venue<input className="field mt-1" name="venue" maxLength="200" value={form.venue} onChange={update} /></label>
      <label className="text-sm font-medium">Organizer<input className="field mt-1" name="organizer" maxLength="200" value={form.organizer} onChange={update} /></label>
      <label className="text-sm font-medium">Audience<input className="field mt-1" name="audience" placeholder="ALL or STUDENT,FACULTY" required value={form.audience} onChange={update} /></label>
      <label className="text-sm font-medium">Department (optional)<input className="field mt-1" name="department_code" maxLength="8" value={form.department_code} onChange={update} /></label>
      <label className="text-sm font-medium sm:col-span-2 xl:col-span-4">Description<textarea className="field mt-1 min-h-24" name="description" maxLength="10000" value={form.description} onChange={update} /></label>
      <div className="flex gap-2 sm:col-span-2 xl:col-span-4"><button className="btn-primary" disabled={busy}>{busy ? 'Saving…' : editing ? 'Update event' : 'Create event'}</button>{editing && <button type="button" className="btn-secondary" onClick={() => { setEditing(null); setForm(blank); }}>Cancel edit</button>}</div>
    </form>
    {loading ? <LoadingSkeleton rows={5} /> : error ? <ErrorState error={error} retry={() => setRevision(value => value + 1)} /> :
      <DashboardCard title="Event calendar"><DataTable rows={data.events} empty="No campus events created." columns={[
        { key: 'event_date', label: 'Date' }, { key: 'title', label: 'Title' },
        { key: 'audience', label: 'Audience' }, { label: 'Department', render: row => row.department_code || 'All' },
        { label: 'Status', render: row => <StatusBadge status={row.status === 'PUBLISHED' ? 'success' : row.status === 'CANCELLED' ? 'error' : 'warning'}>{row.status}</StatusBadge> },
        { label: 'Actions', render: row => <div className="flex flex-wrap gap-2"><button className="text-xs font-semibold text-primary" disabled={row.status === 'CANCELLED' || busy} onClick={() => beginEdit(row)}>Edit</button>{row.status === 'DRAFT' && <button className="text-xs font-semibold text-emerald-700" disabled={busy} onClick={() => action(row.id, 'publish')}>Publish</button>}{row.status !== 'CANCELLED' && <button className="text-xs font-semibold text-red-700" disabled={busy} onClick={() => action(row.id, 'cancel')}>Cancel</button>}</div> },
      ]} /></DashboardCard>}
    <Toast message={toast} type={toast.toLowerCase().includes('failed') || toast.toLowerCase().includes('error') ? 'error' : 'success'} onClose={() => setToast('')} />
  </>;
}
