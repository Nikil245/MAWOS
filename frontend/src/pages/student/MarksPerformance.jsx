import { useMemo, useState } from 'react';
import { Bar, BarChart, Cell, Legend, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { DashboardCard, EmptyState } from '../../components/ui';

export const PASS_PERCENTAGE = 40;

const validNumber = (value) => Number.isFinite(Number(value));
const shortName = (subject) => String(subject || '').slice(0, 10);

// The student dashboard API exposes `internals` and, additively,
// `assessment_details` with the MarksRecord max_marks values.  This adapter
// deliberately discards invalid records instead of treating them as marks.
export function mapMarksForPerformance(marks = [], assessment = 'all') {
  return marks.flatMap((subject) => {
    const details = subject?.assessment_details || {};
    const keys = assessment === 'all' ? Object.keys(subject?.internals || details) : [assessment];
    const entries = keys.map((key) => {
      const detail = details[key] || { marks: subject?.internals?.[key], max_marks: 50 };
      const obtained = Number(detail.marks); const maximum = Number(detail.max_marks);
      if (!validNumber(obtained) || !validNumber(maximum) || maximum <= 0 || obtained < 0 || obtained > maximum) return null;
      return { subject: subject.subject, name: subject.name || subject.subject, assessment: key, obtained, maximum, percentage: Number(((obtained / maximum) * 100).toFixed(1)) };
    }).filter(Boolean);
    if (assessment !== 'all') return entries;
    if (!entries.length) return [];
    const obtained = entries.reduce((sum, entry) => sum + entry.obtained, 0) / entries.length;
    const maximum = entries.reduce((sum, entry) => sum + entry.maximum, 0) / entries.length;
    return [{ subject: subject.subject, name: subject.name || subject.subject, assessment: 'CIE average', obtained, maximum, percentage: Number(((obtained / maximum) * 100).toFixed(1)) }];
  }).map((entry) => ({ ...entry, label: shortName(entry.subject), status: entry.percentage < PASS_PERCENTAGE ? 'Failed' : 'Passed' }));
}

export function calculateMarksSummary(rows = []) {
  if (!rows.length) return { average: null, highest: null, lowest: null, passed: 0, failed: 0, status: 'No marks available' };
  const average = rows.reduce((sum, row) => sum + row.percentage, 0) / rows.length;
  const highest = rows.reduce((best, row) => row.percentage > best.percentage ? row : best, rows[0]);
  const lowest = rows.reduce((worst, row) => row.percentage < worst.percentage ? row : worst, rows[0]);
  const passed = rows.filter((row) => row.status === 'Passed').length;
  const failed = rows.length - passed;
  return { average: Number(average.toFixed(1)), highest, lowest, passed, failed, status: failed ? 'Needs attention' : average >= 75 ? 'Strong performance' : 'On track' };
}

function barColor(percentage) { return percentage < PASS_PERCENTAGE ? '#DC2626' : percentage < 50 ? '#D97706' : percentage >= 75 ? '#16A34A' : '#2563EB'; }
function MarksTooltip({ active, payload }) { if (!active || !payload?.length) return null; const row = payload[0].payload; return <div className="rounded-lg border bg-white p-3 text-sm shadow-card"><p className="font-semibold">{row.name}</p><p className="text-muted">{row.assessment}</p><dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1"><dt>Marks</dt><dd className="text-right font-medium">{row.obtained} / {row.maximum}</dd><dt>Percentage</dt><dd className="text-right font-medium">{row.percentage}%</dd><dt>Status</dt><dd className={`text-right font-semibold ${row.status === 'Passed' ? 'text-emerald-700' : 'text-red-700'}`}>{row.status}</dd></dl></div>; }

export default function MarksPerformance({ marks, semester }) {
  const [selectedSemester, setSelectedSemester] = useState(String(semester || '')); const [assessment, setAssessment] = useState('all');
  const assessmentOptions = useMemo(() => [...new Set((marks || []).flatMap((mark) => Object.keys(mark?.internals || mark?.assessment_details || {})))].sort(), [marks]);
  const rows = useMemo(() => mapMarksForPerformance(marks, assessment), [marks, assessment]); const summary = useMemo(() => calculateMarksSummary(rows), [rows]);
  return <DashboardCard title="Marks Performance" className="xl:col-span-3"><p className="-mt-2 mb-4 text-sm text-muted">Subject-wise internal assessment performance, normalized to each record’s maximum marks.</p><div className="mb-5 flex flex-wrap gap-3"><label className="text-sm font-medium">Semester<select aria-label="Semester" className="field mt-1 min-w-32" value={selectedSemester} onChange={(event) => setSelectedSemester(event.target.value)}><option value={String(semester || '')}>Semester {semester || '—'}</option></select></label><label className="text-sm font-medium">Assessment<select aria-label="Assessment type" className="field mt-1 min-w-44" value={assessment} onChange={(event) => setAssessment(event.target.value)}><option value="all">All assessments (average)</option>{assessmentOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select></label></div>{!rows.length ? <EmptyState title="No valid marks available" detail="Marks will appear here when your internal assessments are published." /> : <><div className="min-w-0 overflow-hidden"><div className="h-80 min-w-0" data-testid="marks-performance-chart"><ResponsiveContainer width="100%" height="100%" minWidth={0}><BarChart data={rows} margin={{ top: 12, right: 8, left: -18, bottom: 4 }}><XAxis dataKey="label" tick={{ fontSize: 11 }} interval="preserveStartEnd" /><YAxis domain={[0, 100]} width={40} tickFormatter={(value) => `${value}%`} /><Tooltip content={<MarksTooltip />} /><Legend formatter={() => 'Performance percentage'} /><ReferenceLine y={PASS_PERCENTAGE} stroke="#D97706" strokeDasharray="4 4" label={{ value: 'Pass 40%', fill: '#92400E', fontSize: 12 }} /><Bar dataKey="percentage" name="Performance percentage" radius={[6, 6, 0, 0]}>{rows.map((row) => <Cell key={`${row.subject}-${row.assessment}`} fill={barColor(row.percentage)} />)}</Bar></BarChart></ResponsiveContainer></div></div><div className="mt-5 grid gap-3 border-t pt-4 sm:grid-cols-2 lg:grid-cols-5"><Summary label="Average" value={`${summary.average}%`} /><Summary label="Highest" value={`${summary.highest.subject} · ${summary.highest.percentage}%`} /><Summary label="Lowest" value={`${summary.lowest.subject} · ${summary.lowest.percentage}%`} /><Summary label="Passed" value={summary.passed} /><Summary label="Overall" value={summary.status} detail={summary.failed ? `${summary.failed} failed` : 'No failed subjects'} /></div></>}</DashboardCard>;
}
function Summary({ label, value, detail }) { return <div><p className="text-xs font-medium uppercase tracking-wide text-muted">{label}</p><p className="mt-1 text-sm font-semibold">{value}</p>{detail && <p className="mt-1 text-xs text-muted">{detail}</p>}</div>; }
