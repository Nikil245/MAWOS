import { useEffect, useMemo, useRef, useState } from 'react';
import { BookOpenCheck, CheckCircle2, ClipboardPenLine, Users } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { api } from '../../services/api';
import { useApi } from '../../hooks/useApi';
import { ConfirmDialog, DashboardCard, ErrorState, LoadingSkeleton, PageHeader, StatCard, StatusBadge, Toast } from '../../components/ui';

const today = new Date().toISOString().slice(0, 10);
export default function FacultyDashboard() {
  const { token, user } = useAuth(); const { data, loading, error } = useApi(() => api.facultyOverview(token), [token]); const [assignmentIndex, setAssignmentIndex] = useState(0); const [panel, setPanel] = useState(null); const [notice, setNotice] = useState(''); const [activity, setActivity] = useState([]);
  const assignment = data?.assignments[assignmentIndex]; const [roster, setRoster] = useState([]); const [rosterError, setRosterError] = useState('');
  useEffect(() => { if (!assignment) return; setRoster([]); api.roster(token, assignment.dept, assignment.year, assignment.section).then((r) => setRoster(r.roster)).catch((err) => setRosterError(err.message)); }, [token, assignment]);
  if (loading) return <LoadingSkeleton rows={6} />; if (error) return <ErrorState error={error} />;
  const students = roster.length; const average = students ? Math.round(roster.reduce((sum, s) => sum + s.attendance, 0) / students) : '—';
  return <><PageHeader title={`Welcome, ${user.name}`} eyebrow={`Faculty workspace / ${user.dept || 'Department'}`}><p className="mt-1 text-sm text-muted">Only your assigned subject-sections can be opened or modified.</p></PageHeader><div className="grid gap-4 md:grid-cols-3"><StatCard label="Assigned subjects" value={data.assignments.length} detail="Current timetable assignments" icon={BookOpenCheck} /><StatCard label="Students in section" value={students || '—'} detail={assignment ? `${assignment.dept} Year ${assignment.year} · ${assignment.section}` : 'No assignment'} icon={Users} tone="teal" /><StatCard label="Average attendance" value={average === '—' ? '—' : `${average}%`} detail="Selected class roster" icon={ClipboardPenLine} tone="green" /></div><div className="mt-4 grid gap-4 xl:grid-cols-3"><DashboardCard title="Assigned subject-sections" className="xl:col-span-2"><div className="grid gap-3 md:grid-cols-2">{data.assignments.map((item, index) => <button className={`rounded-lg border p-4 text-left ${index === assignmentIndex ? 'border-primary bg-blue-50' : 'hover:bg-slate-50'}`} onClick={() => { setAssignmentIndex(index); setPanel(null); }} key={`${item.subject}-${item.section}`}><div className="flex items-center justify-between"><p className="font-semibold">{item.subject}</p><StatusBadge status="info">{item.credits} hrs/wk</StatusBadge></div><p className="mt-1 text-sm text-muted">{item.subject_name}</p><p className="mt-3 text-xs text-muted">{item.dept} · Year {item.year} · Section {item.section}</p></button>)}</div></DashboardCard><DashboardCard title="Recent activity"><div className="space-y-3">{activity.length ? activity.map((item, i) => <div className="flex gap-2 text-sm" key={i}><CheckCircle2 className="mt-0.5 shrink-0 text-emerald-600" size={16} /><span>{item}</span></div>) : <p className="text-sm text-muted">No submissions in this session.</p>}</div></DashboardCard><DashboardCard title="Class actions" className="xl:col-span-2"><div className="flex flex-wrap gap-2"><button className="btn-primary" disabled={!assignment || panel === 'attendance'} onClick={() => setPanel(panel === 'attendance' ? null : 'attendance')}>Mark attendance</button><button className="btn-secondary" disabled={!assignment || panel === 'marks'} onClick={() => setPanel(panel === 'marks' ? null : 'marks')}>Enter marks</button></div>{rosterError && <p className="mt-3 text-sm text-red-600">403: {rosterError}</p>}{panel === 'attendance' && <AttendancePanel token={token} assignment={assignment} roster={roster} onSubmitted={(text) => { setNotice(text); setActivity((a) => [text, ...a]); setPanel(null); }} />}{panel === 'marks' && <MarksPanel token={token} assignment={assignment} roster={roster} onSubmitted={(text) => { setNotice(text); setActivity((a) => [text, ...a]); setPanel(null); }} />}</DashboardCard><DashboardCard title="Department notices"><div className="space-y-3">{data.notifications.slice(0, 4).map((note, i) => <div className="border-b pb-3 last:border-0" key={note.id || i}><p className="text-sm font-medium">{note.title}</p><p className="text-xs text-muted">{note.message}</p></div>)}</div></DashboardCard></div><Toast message={notice} onClose={() => setNotice('')} /></>;
}

function AttendancePanel({ token, assignment, roster, onSubmitted }) {
  const [absent, setAbsent] = useState(new Set()); const [date, setDate] = useState(today); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [locked, setLocked] = useState(false);
  const toggle = (usn) => setAbsent((old) => { const next = new Set(old); next.has(usn) ? next.delete(usn) : next.add(usn); return next; });
  const submit = async () => { setBusy(true); setError(''); try { const r = await api.attendance(token, { dept: assignment.dept, year: assignment.year, section: assignment.section, subject_code: assignment.subject, date, absent_usns: [...absent] }); if (!r.accepted) { setLocked(true); throw new Error('Attendance was already submitted for this subject and date.'); } window.dispatchEvent(new Event('mawos:notifications-changed')); setLocked(true); onSubmitted(`Attendance submitted for ${assignment.subject} at ${new Date().toLocaleTimeString()}.`); } catch (err) { setError(err.message); } finally { setBusy(false); } };
  return <div className="mt-5 rounded-lg border bg-slate-50 p-4"><div className="flex flex-wrap items-end justify-between gap-3"><div><p className="font-semibold">Attendance sheet · {assignment.subject}</p><p className="text-xs text-muted">Present: {roster.length - absent.size} · Absent: {absent.size}</p></div><label className="text-sm">Date<input className="field mt-1" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label></div><button className="btn-secondary mt-3" onClick={() => setAbsent(new Set())}>Mark all present</button><div className="mt-3 max-h-72 overflow-auto rounded-lg border bg-white"><table className="w-full text-sm"><thead className="sticky top-0 bg-white text-left text-xs uppercase text-muted"><tr><th className="p-2">Present</th><th>USN</th><th>Name</th><th>Attendance</th></tr></thead><tbody>{roster.map((student) => <tr className="border-t" key={student.usn}><td className="p-2"><input aria-label={`Mark ${student.name} present`} type="checkbox" checked={!absent.has(student.usn)} onChange={() => toggle(student.usn)} /></td><td>{student.usn}</td><td>{student.name}</td><td>{student.attendance}%</td></tr>)}</tbody></table></div>{error && <p role="alert" className="mt-3 text-sm text-red-600">{error}</p>}<button className="btn-primary mt-4" disabled={busy || locked || !roster.length} onClick={submit}>{locked ? 'Submission locked' : busy ? 'Submitting…' : 'Submit attendance'}</button></div>;
}

export function validateMarksDraft(roster, marksDraft, maximum) {
  const errors = {};
  let missingCount = 0;
  for (const student of roster) {
    const raw = marksDraft[student.usn];
    if (raw === '' || raw === null || raw === undefined) {
      errors[student.usn] = 'Mark is required.';
      missingCount += 1;
      continue;
    }
    const mark = Number(raw);
    if (!Number.isFinite(mark)) errors[student.usn] = 'Enter a numeric mark.';
    else if (mark < 0) errors[student.usn] = 'Mark cannot be negative.';
    else if (mark > maximum) errors[student.usn] = `Mark cannot exceed ${maximum}.`;
  }
  return { errors, missingCount };
}

export function MarksPanel({ token, assignment, roster, onSubmitted }) {
  const { data: policy, loading: policyLoading, error: policyError } = useApi(() => api.marksPolicy(token), [token]);
  const [internal, setInternal] = useState(null);
  const [marksDraft, setMarksDraft] = useState({});
  const [touched, setTouched] = useState({});
  const [attempted, setAttempted] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const inputRefs = useRef({});
  const assessments = Array.isArray(policy?.assessments) ? policy.assessments : [];
  const selectedInternal = internal ?? assessments[0]?.internal;
  const assessment = assessments.find((item) => item.internal === selectedInternal);
  const maximum = assessment?.max_marks;
  const policyIsValid = assessments.length > 0 && assessments.every((item) => Number.isInteger(item.internal)
    && typeof item.label === 'string' && Number.isFinite(item.max_marks) && item.max_marks >= 0);
  const validation = useMemo(() => Number.isFinite(maximum)
    ? validateMarksDraft(roster, marksDraft, maximum) : { errors: {}, missingCount: 0 }, [roster, marksDraft, maximum]);

  if (policyLoading) return <LoadingSkeleton rows={3} />;
  if (policyError || !policyIsValid || !assessment) return <ErrorState error={policyError || new Error('Marks policy is unavailable or invalid.')} />;

  const focusFirstInvalid = () => {
    const firstUsn = roster.find((student) => validation.errors[student.usn])?.usn;
    inputRefs.current[firstUsn]?.focus();
  };
  const review = () => {
    setAttempted(true);
    setError('');
    if (Object.keys(validation.errors).length) {
      focusFirstInvalid();
      return;
    }
    setConfirm(true);
  };
  const save = async () => {
    setAttempted(true);
    setError('');
    if (Object.keys(validation.errors).length) {
      setConfirm(false);
      focusFirstInvalid();
      return;
    }
    setBusy(true);
    try {
      const entries = roster.map((student) => ({ usn: student.usn, marks: Number(marksDraft[student.usn]) }));
      const result = await api.marks(token, { subject_code: assignment.subject, internal: selectedInternal, entries });
      onSubmitted(`${result.accepted} marks entries saved for ${assignment.subject}.`);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
      setConfirm(false);
    }
  };
  const updateDraft = (usn, value) => {
    setMarksDraft((draft) => ({ ...draft, [usn]: value }));
    setTouched((current) => ({ ...current, [usn]: true }));
  };

  return <div className="mt-5 rounded-lg border bg-slate-50 p-4">
    <div className="flex flex-wrap gap-3"><label className="text-sm">Assessment<select className="field mt-1" value={selectedInternal} onChange={(event) => setInternal(Number(event.target.value))}>{assessments.map((item) => <option key={item.internal} value={item.internal}>{item.label}</option>)}</select></label><p className="pt-6 text-sm font-medium">Maximum marks: {maximum}</p></div>
    <div className="mt-4 max-h-72 overflow-auto rounded-lg border bg-white"><table className="w-full text-sm"><thead className="sticky top-0 bg-white text-left text-xs uppercase text-muted"><tr><th className="p-2">USN</th><th>Name</th><th>Marks / {maximum}</th></tr></thead><tbody>{roster.map((student) => { const fieldError = validation.errors[student.usn]; const showError = fieldError && (attempted || touched[student.usn]); return <tr className="border-t" key={student.usn}><td className="p-2">{student.usn}</td><td>{student.name}</td><td><input ref={(node) => { inputRefs.current[student.usn] = node; }} aria-label={`Marks for ${student.name}`} aria-invalid={Boolean(showError)} className="field w-24 !py-1" type="number" min="0" max={maximum} value={marksDraft[student.usn] ?? ''} onChange={(event) => updateDraft(student.usn, event.target.value)} onBlur={() => setTouched((current) => ({ ...current, [student.usn]: true }))} />{showError && <p role="alert" className="mt-1 text-xs text-red-600">{fieldError}</p>}</td></tr>; })}</tbody></table></div>
    {attempted && validation.missingCount > 0 && <p className="mt-3 text-sm text-red-600">{validation.missingCount} {validation.missingCount === 1 ? 'student still requires' : 'students still require'} marks.</p>}
    {error && <p role="alert" className="mt-3 text-sm text-red-600">{error}</p>}
    <button className="btn-primary mt-4" disabled={busy || !roster.length} onClick={review}>Review and submit</button>
    <ConfirmDialog open={confirm} title="Submit marks?" confirmLabel="Save marks" busy={busy} onClose={() => setConfirm(false)} onConfirm={save}>This writes {assessment.label} marks out of {maximum} for {roster.length} students. The server will verify your assignment scope.</ConfirmDialog>
  </div>;
}
