import { AlertTriangle, Bot, CheckCircle2, ExternalLink, Info, Library, MapPin } from 'lucide-react';
import { Link } from 'react-router-dom';

const BLOCK_TYPES = new Set([
  'text', 'metric_cards', 'table', 'bullet_list', 'status_notice', 'empty_state',
  'link_action', 'warning', 'placement_cards', 'library_book_cards', 'event_cards',
  'timetable', 'marks_summary', 'attendance_summary',
]);
const TONES = new Set(['neutral', 'info', 'success', 'warning', 'danger']);

function text(value, max = 1000, required = false) {
  if (typeof value !== 'string') return required ? null : undefined;
  const cleaned = value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '').trim();
  if ((required && !cleaned) || cleaned.length > max) return null;
  return cleaned || undefined;
}

function number(value, { min = -Infinity, max = Infinity } = {}) {
  return typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max ? value : null;
}

export function safeAssistantLink(value) {
  if (typeof value !== 'string' || !value || value.length > 2048 || /[\u0000-\u001f\\]/.test(value)) return null;
  if (value.startsWith('/') && !value.startsWith('//')) return value;
  try {
    const parsed = new URL(value);
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) return null;
    const host = parsed.hostname.toLowerCase();
    if (host === 'localhost' || host.endsWith('.local') || /^(127\.|0\.|10\.|192\.168\.|169\.254\.)/.test(host)
      || /^172\.(1[6-9]|2\d|3[01])\./.test(host)) return null;
    return parsed.href;
  } catch {
    return null;
  }
}

function safeMetricCards(block) {
  if (!Array.isArray(block.cards) || !block.cards.length || block.cards.length > 8) return null;
  const cards = block.cards.map((card) => {
    const label = text(card?.label, 80, true); const value = text(card?.value, 80, true);
    if (!label || !value || (card.tone && !TONES.has(card.tone))) return null;
    return { label, value, detail: text(card.detail, 160), tone: card.tone || 'neutral' };
  });
  return cards.every(Boolean) ? { type: block.type, title: text(block.title, 120), cards } : null;
}

function safeTable(block) {
  if (!Array.isArray(block.columns) || !block.columns.length || block.columns.length > 12
    || !Array.isArray(block.rows) || block.rows.length > 100) return null;
  const columns = block.columns.map((column) => {
    const key = text(column?.key, 40, true); const label = text(column?.label, 80, true);
    return key && /^[a-z][a-z0-9_]{0,39}$/.test(key) && label ? { key, label } : null;
  });
  if (!columns.every(Boolean) || new Set(columns.map(({ key }) => key)).size !== columns.length) return null;
  const keys = new Set(columns.map(({ key }) => key));
  const rows = block.rows.map((row) => {
    if (!row || typeof row !== 'object' || Array.isArray(row) || Object.keys(row).some((key) => !keys.has(key))) return null;
    const safe = {};
    for (const { key } of columns) {
      const value = row[key];
      if (value === null || ['string', 'number', 'boolean'].includes(typeof value)) safe[key] = String(value ?? '—').slice(0, 1000);
      else return null;
    }
    return safe;
  });
  const caption = text(block.caption, 240, true);
  return rows.every(Boolean) && caption ? { type: block.type, title: text(block.title, 120), caption, columns, rows } : null;
}

function safeList(block) {
  if (!Array.isArray(block.items) || !block.items.length || block.items.length > 20) return null;
  const items = block.items.map((item) => text(item, 600, true));
  return items.every(Boolean) ? { type: block.type, title: text(block.title, 120), items } : null;
}

function safeCards(block, key, fields, max = 30) {
  if (!Array.isArray(block[key]) || !block[key].length || block[key].length > max) return null;
  const items = block[key].map((item) => {
    if (!item || typeof item !== 'object') return null;
    const safe = {};
    for (const [name, config] of Object.entries(fields)) {
      const value = item[name];
      if (config.kind === 'number') {
        const parsed = number(value, config);
        if (parsed === null && config.required) return null;
        if (parsed !== null) safe[name] = parsed;
      } else if (config.kind === 'object') {
        if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
        const entries = Object.entries(value).slice(0, 10);
        if (entries.some(([entryKey, entryValue]) => !text(entryKey, 40, true) || number(entryValue) === null)) return null;
        safe[name] = Object.fromEntries(entries);
      } else if (config.kind === 'link') {
        const link = value == null ? undefined : safeAssistantLink(value);
        if (value != null && !link) return null;
        if (link) safe[name] = link;
      } else {
        const parsed = text(value, config.max || 240, config.required);
        if (parsed === null) return null;
        if (parsed !== undefined) safe[name] = parsed;
      }
    }
    return safe;
  });
  return items.every(Boolean) ? { type: block.type, title: text(block.title, 120), [key]: items } : null;
}

function sanitizeBlock(block) {
  if (!block || typeof block !== 'object' || !BLOCK_TYPES.has(block.type)) return null;
  if (block.type === 'text') {
    const content = text(block.content, 6000, true);
    return content ? { type: block.type, heading: text(block.heading, 120), content } : null;
  }
  if (block.type === 'metric_cards') return safeMetricCards(block);
  if (block.type === 'table') return safeTable(block);
  if (block.type === 'bullet_list') return safeList(block);
  if (block.type === 'status_notice') {
    const title = text(block.title, 120, true); const message = text(block.message, 1000, true);
    return title && message && ['info', 'success', 'warning', 'error'].includes(block.status)
      ? { type: block.type, title, message, status: block.status } : null;
  }
  if (block.type === 'empty_state' || block.type === 'warning') {
    const title = text(block.title, 120, true); const message = text(block.message, 1000, true);
    return title && message ? { type: block.type, title, message } : null;
  }
  if (block.type === 'link_action') {
    const label = text(block.label, 100, true); const url = safeAssistantLink(block.url);
    return label && url ? { type: block.type, label, url, description: text(block.description, 240), external: Boolean(block.external) } : null;
  }
  if (block.type === 'placement_cards') return safeCards(block, 'cards', {
    company: { required: true, max: 128 }, role: { required: true, max: 128 }, package: { max: 80 },
    date: { max: 80 }, deadline: { max: 80 }, eligibility: { required: true, max: 120 },
    status: { max: 80 }, apply_url: { kind: 'link' },
  }, 20);
  if (block.type === 'library_book_cards') return safeCards(block, 'books', {
    title: { required: true, max: 240 }, author: { required: true, max: 240 }, category: { required: true, max: 120 },
    available_copies: { kind: 'number', required: true, min: 0 }, total_copies: { kind: 'number', min: 0 },
    availability: { required: true, max: 80 }, isbn: { max: 32 },
  }, 20);
  if (block.type === 'event_cards') return safeCards(block, 'events', {
    title: { required: true, max: 240 }, date: { required: true, max: 80 }, time: { max: 80 }, venue: { max: 160 },
    organizer: { max: 160 }, description: { max: 1000 },
  }, 30);
  if (block.type === 'timetable') return safeCards(block, 'entries', {
    day: { required: true, max: 40 }, period: { required: true, max: 80 }, subject: { required: true, max: 160 },
    time: { max: 80 }, room: { max: 80 }, faculty: { max: 160 },
  }, 100);
  if (block.type === 'marks_summary') return safeCards(block, 'subjects', {
    subject_code: { required: true, max: 40 }, subject_name: { required: true, max: 160 },
    internals: { kind: 'object' }, cie_average: { kind: 'number' },
  }, 50);
  if (block.type === 'attendance_summary') {
    const overall = number(block.overall_percentage, { min: 0, max: 100 });
    const threshold = number(block.threshold_percentage, { min: 0, max: 100 });
    const wrapped = safeCards({ type: block.type, subjects: block.subjects }, 'subjects', {
      subject_code: { required: true, max: 40 }, subject_name: { required: true, max: 160 },
      attended: { kind: 'number', required: true, min: 0 }, total: { kind: 'number', required: true, min: 0 },
      percentage: { kind: 'number', required: true, min: 0, max: 100 }, status: { required: true, max: 40 },
    }, 50);
    return overall !== null && threshold !== null && wrapped
      ? { type: block.type, overall_percentage: overall, threshold_percentage: threshold, subjects: wrapped.subjects } : null;
  }
  return null;
}

export function safeAssistantBlocks(value) {
  if (!Array.isArray(value) || value.length > 20) return null;
  const blocks = value.map(sanitizeBlock);
  return blocks.every(Boolean) ? blocks : null;
}

function title(block) {
  return block.title ? <h4 className="mb-2 text-sm font-semibold text-slate-900">{block.title}</h4> : null;
}

const toneClass = {
  neutral: 'border-slate-200 bg-white', info: 'border-blue-200 bg-blue-50/60',
  success: 'border-emerald-200 bg-emerald-50/70', warning: 'border-amber-200 bg-amber-50/70',
  danger: 'border-red-200 bg-red-50/70',
};

function StructuredTable({ block }) {
  return <section className="mt-3" aria-label={block.title || block.caption}>
    {title(block)}
    <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
      <table className="min-w-full text-left text-xs">
        <caption className="sr-only">{block.caption}</caption>
        <thead className="bg-slate-50 text-slate-600"><tr>{block.columns.map((column) => <th className="whitespace-nowrap px-3 py-2 font-semibold" scope="col" key={column.key}>{column.label}</th>)}</tr></thead>
        <tbody className="divide-y divide-slate-100">{block.rows.map((row, rowIndex) => <tr key={rowIndex}>{block.columns.map((column) => <td className="whitespace-nowrap px-3 py-2 align-top text-slate-700" key={column.key}>{row[column.key]}</td>)}</tr>)}</tbody>
      </table>
    </div>
  </section>;
}

function AssistantBlock({ block }) {
  if (block.type === 'text') return <section className="mt-3"><>{block.heading && <h4 className="mb-1 text-sm font-semibold">{block.heading}</h4>}</><p className="whitespace-pre-line text-sm leading-6 text-slate-700">{block.content}</p></section>;
  if (block.type === 'metric_cards') return <section className="mt-3" aria-label={block.title || 'Metrics'}>{title(block)}<div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">{block.cards.map((card) => <div className={`rounded-xl border p-3 ${toneClass[card.tone]}`} key={`${card.label}:${card.value}`}><p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{card.label}</p><p className="mt-1 text-xl font-bold text-slate-900">{card.value}</p>{card.detail && <p className="mt-1 text-xs text-slate-600">{card.detail}</p>}</div>)}</div></section>;
  if (block.type === 'table') return <StructuredTable block={block} />;
  if (block.type === 'bullet_list') return <section className="mt-3">{title(block)}<ul className="list-disc space-y-1 pl-5 text-sm text-slate-700">{block.items.map((item, index) => <li key={`${index}:${item}`}>{item}</li>)}</ul></section>;
  if (block.type === 'status_notice') {
    const classes = block.status === 'success' ? toneClass.success : block.status === 'error' ? toneClass.danger : block.status === 'warning' ? toneClass.warning : toneClass.info;
    return <div className={`mt-3 rounded-xl border p-3 ${classes}`} role={block.status === 'error' ? 'alert' : 'status'}><p className="font-semibold text-slate-900">{block.title}</p><p className="mt-1 text-sm text-slate-700">{block.message}</p></div>;
  }
  if (block.type === 'warning') return <div className={`mt-3 flex gap-2 rounded-xl border p-3 ${toneClass.warning}`} role="alert"><AlertTriangle className="mt-0.5 shrink-0 text-amber-700" size={17} aria-hidden="true" /><div><p className="font-semibold text-slate-900">{block.title}</p><p className="mt-1 text-sm text-slate-700">{block.message}</p></div></div>;
  if (block.type === 'empty_state') return <div className="mt-3 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4 text-center"><Info className="mx-auto text-slate-400" size={20} aria-hidden="true" /><p className="mt-1 font-semibold text-slate-800">{block.title}</p><p className="mt-1 text-xs text-slate-600">{block.message}</p></div>;
  if (block.type === 'link_action') {
    const className = 'mt-3 inline-flex items-center gap-1 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm font-semibold text-primary hover:bg-blue-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary';
    return block.url.startsWith('/') ? <Link className={className} to={block.url}>{block.label}{block.description && <span className="sr-only"> — {block.description}</span>}</Link> : <a className={className} href={block.url} target="_blank" rel="noreferrer">{block.label}<ExternalLink size={14} aria-hidden="true" /></a>;
  }
  if (block.type === 'placement_cards') return <section className="mt-3" aria-label={block.title || 'Placement opportunities'}>{title(block)}<div className="grid gap-3 md:grid-cols-2">{block.cards.map((card, index) => <article className="rounded-xl border border-slate-200 bg-white p-3" key={`${card.company}:${card.role}:${index}`}><div className="flex items-start justify-between gap-2"><div><p className="font-semibold text-slate-900">{card.company}</p><p className="text-sm text-slate-700">{card.role}</p></div><span className="rounded-full bg-slate-100 px-2 py-1 text-[11px] font-semibold text-slate-700">{card.eligibility}</span></div><dl className="mt-3 grid grid-cols-2 gap-2 text-xs"><div><dt className="text-slate-500">Package</dt><dd className="font-semibold">{card.package || 'Not listed'}</dd></div><div><dt className="text-slate-500">Deadline</dt><dd className="font-semibold">{card.deadline || 'Not listed'}</dd></div></dl>{card.apply_url && <a className="mt-3 inline-flex items-center gap-1 text-xs font-semibold text-primary underline" href={card.apply_url} target="_blank" rel="noreferrer">Open safe application link <ExternalLink size={12} aria-hidden="true" /></a>}</article>)}</div></section>;
  if (block.type === 'library_book_cards') return <section className="mt-3" aria-label={block.title || 'Library books'}>{title(block)}<div className="grid gap-3 sm:grid-cols-2">{block.books.map((book, index) => <article className="rounded-xl border border-slate-200 bg-white p-3" key={`${book.isbn || book.title}:${index}`}><div className="flex gap-2"><Library className="mt-0.5 shrink-0 text-primary" size={17} aria-hidden="true" /><div><p className="font-semibold text-slate-900">{book.title}</p><p className="text-xs text-slate-600">{book.author} · {book.category}</p></div></div><p className="mt-3 text-xs font-semibold text-slate-700">{book.available_copies} available{book.total_copies !== undefined ? ` of ${book.total_copies}` : ''} · {book.availability}</p></article>)}</div></section>;
  if (block.type === 'event_cards') return <section className="mt-3" aria-label={block.title || 'Events'}>{title(block)}<div className="grid gap-3 md:grid-cols-2">{block.events.map((event, index) => <article className="rounded-xl border border-slate-200 bg-white p-3" key={`${event.title}:${event.date}:${index}`}><p className="font-semibold text-slate-900">{event.title}</p><p className="mt-1 text-xs text-slate-600">{event.date}{event.time ? ` · ${event.time}` : ''}</p>{event.venue && <p className="mt-1 flex items-center gap-1 text-xs text-slate-600"><MapPin size={12} aria-hidden="true" />{event.venue}</p>}{event.description && <p className="mt-2 text-xs text-slate-700">{event.description}</p>}</article>)}</div></section>;
  if (block.type === 'timetable') return <StructuredTable block={{ ...block, caption: block.title || 'Authorized timetable', columns: [{ key: 'day', label: 'Day' }, { key: 'period', label: 'Period' }, { key: 'subject', label: 'Subject' }, { key: 'time', label: 'Time' }, { key: 'room', label: 'Room' }], rows: block.entries.map((entry) => ({ day: entry.day, period: entry.period, subject: entry.subject, time: entry.time || '—', room: entry.room || '—' })) }} />;
  if (block.type === 'marks_summary') return <StructuredTable block={{ ...block, caption: block.title || 'Internal marks', columns: [{ key: 'subject_code', label: 'Code' }, { key: 'subject_name', label: 'Subject' }, { key: 'internals_display', label: 'Internals' }, { key: 'average', label: 'CIE average' }], rows: block.subjects.map((subject) => ({ subject_code: subject.subject_code, subject_name: subject.subject_name, internals_display: Object.entries(subject.internals).map(([key, value]) => `${key}: ${value}`).join(' · ') || '—', average: subject.cie_average ?? '—' })) }} />;
  if (block.type === 'attendance_summary') return <section className="mt-3" aria-label="Attendance status by subject"><div className="space-y-2">{block.subjects.map((subject) => <div className="rounded-lg border border-slate-200 bg-white p-2" key={subject.subject_code}><div className="flex items-center justify-between gap-3 text-xs"><span className="font-semibold text-slate-800">{subject.subject_code} · {subject.subject_name}</span><span>{subject.percentage.toFixed(1)}% · {subject.status}</span></div><div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100" role="progressbar" aria-label={`${subject.subject_name} attendance`} aria-valuemin="0" aria-valuemax="100" aria-valuenow={subject.percentage}><span className={`block h-full rounded-full ${subject.percentage < block.threshold_percentage ? 'bg-amber-500' : 'bg-emerald-500'}`} style={{ width: `${subject.percentage}%` }} /></div></div>)}</div></section>;
  return null;
}

function SafeTrace({ trace, role }) {
  if (!trace || trace.visibility === 'simple') return trace?.summary ? <p className="mt-3 flex items-center gap-1 text-[11px] text-slate-500"><CheckCircle2 size={12} aria-hidden="true" />{trace.summary}</p> : null;
  if (!['admin', 'hod', 'principal', 'faculty'].includes(role)) return null;
  return <details className="mt-3 hidden rounded-lg border border-slate-200 bg-slate-50 p-2 text-xs text-slate-600 md:block"><summary className="cursor-pointer font-semibold text-slate-700">How this answer was prepared</summary><p className="mt-2">{trace.summary} · approximately {Math.round(trace.duration_ms)} ms</p><ol className="mt-2 space-y-1">{trace.steps.map((step, index) => <li key={`${step.label}:${index}`}><span className="font-semibold">{step.label}:</span> {step.detail}</li>)}</ol></details>;
}

export default function AssistantResponse({ message, role, onSuggestion }) {
  return <div className="flex max-w-[95%] items-start gap-2 sm:max-w-[88%]">
    <span className="mt-1 shrink-0 rounded-lg bg-blue-50 p-2 text-primary" aria-hidden="true"><Bot size={17} /></span>
    <article className="min-w-0 flex-1 rounded-2xl rounded-tl-md border border-slate-200 bg-slate-50 p-3 text-sm shadow-sm" aria-label="MAWOS assistant answer">
      <p className="font-medium leading-6 text-slate-900">{message.summary || message.text}</p>
      {message.blocks?.map((block, index) => <AssistantBlock block={block} key={`${block.type}:${index}`} />)}
      {message.candidates?.length > 1 && <ol aria-label="Library candidates" className="mt-3 space-y-2">{message.candidates.map((book, index) => <li className="rounded-lg border border-slate-200 bg-white p-2" key={book.isbn}><p className="font-semibold">{index + 1}. {book.title}</p><p className="text-xs text-muted">{book.author} · {book.category} · ISBN {book.isbn}</p></li>)}</ol>}
      {message.actions?.map((action) => <Link className="mt-2 block w-fit font-semibold text-primary underline" key={`${action.label}:${action.route}`} to={action.route}>{action.label}</Link>)}
      {message.note && <p className="mt-2 text-xs text-amber-800">{message.note}</p>}
      <SafeTrace trace={message.safeTrace} role={role} />
      <p className="mt-3 border-t border-slate-200 pt-2 text-[11px] font-semibold text-slate-500">{message.source}</p>
      {message.suggestions?.length > 0 && <div className="mt-3 flex flex-wrap gap-2" aria-label="Suggested follow-up queries">{message.suggestions.map((suggestion) => <button type="button" className="rounded-full border border-blue-200 bg-white px-3 py-1.5 text-left text-xs font-medium text-primary hover:bg-blue-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary" key={suggestion} onClick={() => onSuggestion(suggestion)}>{suggestion}</button>)}</div>}
    </article>
  </div>;
}
