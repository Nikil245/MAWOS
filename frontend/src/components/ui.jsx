import { AlertCircle, Bell, CheckCircle2, ChevronLeft, ChevronRight, Inbox, X } from 'lucide-react';
import { useState } from 'react';

export function StatusBadge({ status, children }) {
  const tone = status === 'success' || status === 'paid' || status === 'eligible' ? 'bg-emerald-50 text-emerald-700' :
    status === 'warning' || status === 'pending' || status === 'waitlist' ? 'bg-amber-50 text-amber-700' :
      status === 'error' || status === 'blocked' ? 'bg-red-50 text-red-700' : 'bg-blue-50 text-blue-700';
  return <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${tone}`}>{children || status}</span>;
}

export function PageHeader({ title, eyebrow, actions, children }) {
  return <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
    <div><p className="text-sm text-muted">{eyebrow || 'MAWOS / Workspace'}</p><h1 className="mt-1 text-2xl font-bold tracking-tight">{title}</h1>{children}</div>
    {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
  </div>;
}

export function StatCard({ label, value, detail, icon: Icon, tone = 'blue' }) {
  const colors = { blue: 'bg-blue-50 text-primary', teal: 'bg-teal-50 text-teal', green: 'bg-emerald-50 text-emerald-600', amber: 'bg-amber-50 text-amber-600', red: 'bg-red-50 text-red-600' };
  const shownDetail = label === 'Scholarship' ? 'Current published scholarship workflow' : detail;
  return <div className="card"><div className="flex items-start justify-between"><div><p className="text-sm font-medium text-muted">{label}</p><p className="mt-2 text-2xl font-bold">{value}</p></div>{Icon && <span className={`rounded-lg p-2.5 ${colors[tone]}`}><Icon size={20} /></span>}</div>{shownDetail && <p className="mt-3 text-xs text-muted">{shownDetail}</p>}</div>;
}

export function DashboardCard({ title, action, children, className = '' }) {
  return <section className={`card ${className}`}><div className="mb-4 flex items-center justify-between gap-3"><h2 className="font-semibold">{title}</h2>{action}</div>{children}</section>;
}

export function LoadingSkeleton({ rows = 3 }) { return <div className="space-y-3">{Array.from({ length: rows }, (_, i) => <div key={i} className="h-14 animate-pulse rounded-lg bg-slate-100" />)}</div>; }
export function EmptyState({ title = 'Nothing to show', detail }) { return <div className="py-8 text-center"><Inbox className="mx-auto mb-3 text-slate-300" /><p className="font-medium">{title}</p>{detail && <p className="mt-1 text-sm text-muted">{detail}</p>}</div>; }
export function ErrorState({ error, retry }) { return <div className="rounded-xl border border-red-200 bg-red-50 p-5 text-red-800"><AlertCircle className="mb-2" /><p className="font-semibold">Could not load this data</p><p className="mt-1 text-sm">{error?.message || 'The backend may be offline.'}</p>{retry && <button className="btn-secondary mt-3" onClick={retry}>Try again</button>}</div>; }

export function DataTable({ columns, rows, pageSize = 8, empty = 'No records found.' }) {
  const [query, setQuery] = useState(''); const [page, setPage] = useState(0);
  const filtered = rows.filter((row) => JSON.stringify(row).toLowerCase().includes(query.toLowerCase()));
  const visible = filtered.slice(page * pageSize, page * pageSize + pageSize); const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  return <div><input aria-label="Search table" className="field mb-3 max-w-xs" placeholder="Search records…" value={query} onChange={(e) => { setQuery(e.target.value); setPage(0); }} />
    <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b text-xs uppercase tracking-wide text-muted"><tr>{columns.map((c) => <th className="px-3 py-2 font-semibold" key={c.label}>{c.label}</th>)}</tr></thead><tbody>{visible.map((row, index) => <tr className="border-b last:border-0" key={row.id || row.usn || index}>{columns.map((c) => <td className="px-3 py-3" key={c.label}>{c.render ? c.render(row) : row[c.key]}</td>)}</tr>)}</tbody></table></div>
    {!visible.length && <EmptyState title={empty} />}{filtered.length > pageSize && <div className="mt-3 flex items-center justify-end gap-2 text-sm text-muted"><button aria-label="Previous page" className="btn-secondary !p-2" disabled={!page} onClick={() => setPage(page - 1)}><ChevronLeft size={16} /></button><span>{page + 1} / {pages}</span><button aria-label="Next page" className="btn-secondary !p-2" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}><ChevronRight size={16} /></button></div>}</div>;
}

export function ConfirmDialog({ open, title, children, confirmLabel = 'Confirm', onConfirm, onClose, busy }) {
  if (!open) return null;
  return <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/40 p-4" role="dialog" aria-modal="true" aria-labelledby="dialog-title"><div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl"><div className="flex justify-between"><h2 id="dialog-title" className="text-lg font-bold">{title}</h2><button aria-label="Close" onClick={onClose}><X size={20} /></button></div><div className="mt-3 text-sm text-muted">{children}</div><div className="mt-6 flex justify-end gap-2"><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy} onClick={onConfirm}>{busy ? 'Working…' : confirmLabel}</button></div></div></div>;
}

export function Toast({ message, type = 'success', onClose }) { if (!message) return null; return <div role="status" className={`fixed bottom-5 right-5 z-50 flex max-w-sm items-center gap-2 rounded-lg p-4 text-sm font-medium shadow-lg ${type === 'error' ? 'bg-red-600 text-white' : 'bg-slate-900 text-white'}`}>{type === 'error' ? <AlertCircle size={18} /> : <CheckCircle2 size={18} />}<span>{message}</span><button aria-label="Dismiss notification" onClick={onClose}><X size={16} /></button></div>; }

export function NotificationDrawer({ open, onClose, notifications, loading, error, sessionExpired, retry, onMarkRead, onOpenNotification, onMarkAllRead }) {
  if (!open) return null;
  return <aside aria-label="Notifications panel" className="fixed inset-y-0 right-0 z-40 flex w-full max-w-sm flex-col border-l bg-white p-4 shadow-xl sm:p-5"><div className="flex items-center justify-between"><h2 className="font-bold">Notifications</h2><button onClick={onClose} aria-label="Close notifications"><X /></button></div>{notifications.some(n => !n.read) && <button className="btn-secondary mt-3 self-start" onClick={onMarkAllRead}>Mark all as read</button>}{loading ? <div className="mt-5"><LoadingSkeleton rows={4} /></div> : sessionExpired ? <div className="mt-5"><EmptyState title="Session expired" detail="Sign in again to view notifications." /></div> : error ? <div className="mt-5"><ErrorState error={error} retry={retry} /></div> : notifications.length ? <div className="mt-5 space-y-3 overflow-y-auto">{notifications.map((n, i) => <article className={`border-b p-3 ${n.read ? 'bg-white' : 'rounded-lg bg-blue-50'}`} key={n.id || i}><button type="button" onClick={() => onOpenNotification?.(n)} className="block w-full text-left"><div className="flex items-start justify-between gap-3"><p className="text-sm font-semibold">{n.title}</p><span className="shrink-0 text-xs text-muted">{n.read ? 'Read' : 'Unread'}</span></div><p className="mt-1 text-sm text-muted">{n.message}</p><p className="mt-2 text-xs text-muted">{n.notification_type || n.source_agent} · {new Date(n.created_at || n.at).toLocaleString()}</p></button>{!n.read && <button type="button" className="mt-2 text-xs font-semibold text-primary hover:underline" onClick={() => onMarkRead?.(n.id)}>Mark as read</button>}</article>)}</div> : <EmptyState title="No notifications" detail="Important MAWOS updates will appear here." />}</aside>;
}
export { Bell };
