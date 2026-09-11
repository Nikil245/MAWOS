import { useEffect, useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { api } from '../../services/api';
import { DashboardCard, PageHeader } from '../../components/ui';

const emptyDrive = { company: '', role: '', package_lpa: '', drive_date: '', departments: 'ALL', min_cgpa: 6, max_backlogs: 0, min_attendance: 75, status: 'OPEN', requires_fee_clearance: false, application_deadline: '' };
const editable = ['DRAFT', 'OPEN'];
const active = ['OPEN', 'SHORTLIST_GENERATED'];
const badge = 'inline-block rounded-full bg-slate-100 px-2 py-1 text-xs font-semibold';

function Reasons({ value }) {
  return <ul className="list-disc space-y-1 pl-4">{(value || '').split('; ').filter(Boolean).map((reason, i) => <li key={i}>{reason}</li>)}</ul>;
}

function Evaluation({ entry, admin = false }) {
  return <><span className={badge}>{entry.status === 'NOT_EVALUATED' ? 'Not evaluated' : entry.eligible ? 'Eligible' : 'Not eligible'}</span>
    <p className="my-2 text-sm">{entry.status === 'NOT_EVALUATED' ? 'Hard-filter preview · Rules-only evaluation' : entry.ml_probability != null ? `Model evaluated${admin ? ` · ${entry.ml_probability.toFixed(2)}` : ''}` : entry.eligible ? 'Rules-only evaluation' : 'Rules-only · hard-filter rejection'}</p>
    {admin && <p className="mb-2 text-xs text-muted">Model version: {entry.model_version || '—'}</p>}
    <Reasons value={entry.reasons} /></>;
}

export function StudentPlacements() {
  const { token, user } = useAuth();
  return user?.role === 'student' ? <StudentView key={`${token}:${user.usn}`} token={token} usn={user.usn} /> : null;
}

function StudentView({ token, usn }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let current = true;
    setRows(null); setError('');
    api.placementDrives(token).then(drives => Promise.all(drives.map(drive => api.placementEligibility(token, drive.id, usn))))
      .then(data => { if (current) setRows(data); })
      .catch(err => { if (current) setError(err.message); });
    return () => { current = false; };
  }, [token, usn, retry]);
  return <><PageHeader title="My placements" eyebrow="MAWOS / Careers"><p>Recruitment drives and your own eligibility.</p></PageHeader>
    {error ? <div role="alert">{error} <button className="btn-secondary" onClick={() => setRetry(retry + 1)}>Retry</button></div> : !rows ? <p role="status">Loading placements…</p> : !rows.length ? <p>No placement drives available.</p> :
      <div className="grid gap-4 md:grid-cols-2">{rows.map(entry => <DashboardCard key={entry.drive.id} title={`${entry.drive.company} · ${entry.drive.role}`}>
        <p className="mb-2">₹{entry.drive.package_lpa} LPA · {entry.drive.drive_date}</p><p className="mb-3"><span className={badge}>{entry.drive.status}</span></p>
        <Evaluation entry={entry} />
      </DashboardCard>)}</div>}
  </>;
}

export function AdminPlacements() {
  const { token, user } = useAuth();
  return user?.role === 'admin' ? <AdminView key={token} token={token} /> : null;
}

function AdminView({ token }) {
  const [drives, setDrives] = useState(null);
  const [selected, setSelected] = useState(null);
  const [shortlist, setShortlist] = useState([]);
  const [outcomes, setOutcomes] = useState([]);
  const [form, setForm] = useState({ ...emptyDrive });
  const [editing, setEditing] = useState(null);
  const [outcome, setOutcome] = useState({ usn: '', outcome_status: 'OFFER_MADE', package_offered: '', allow_multiple_offers: false });
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let current = true;
    setError('');
    api.placementDrives(token).then(rows => { if (current) setDrives(rows); }).catch(err => { if (current) setError(err.message); });
    return () => { current = false; };
  }, [token, retry]);

  async function loadDetail(id) {
    const [rows, results] = await Promise.all([api.placementShortlist(token, id), api.placementOutcomes(token, id)]);
    setShortlist(rows); setOutcomes(results); setSelected(id);
  }
  async function perform(work) {
    setBusy(true); setError(''); setSuccess('');
    try { await work(); } catch (err) { setError(err.message); } finally { setBusy(false); }
  }
  const refresh = async () => setDrives(await api.placementDrives(token));
  async function saveDrive(event) {
    event.preventDefault();
    const body = { ...form, company: form.company.trim(), role: form.role.trim(), departments: form.departments.toUpperCase().split(',').map(code => code.trim()).join(','), application_deadline: form.application_deadline || null };
    for (const key of ['package_lpa', 'min_cgpa', 'max_backlogs', 'min_attendance']) body[key] = Number(body[key]);
    const codes = [...new Set(body.departments.split(','))];
    if (codes.some(code => !/^[A-Z0-9]{1,8}$/.test(code)) || (codes.includes('ALL') && codes.length !== 1)) {
      setError('Use comma-separated department codes or ALL. ALL cannot be combined with department codes.'); return;
    }
    body.departments = codes.join(',');
    if (['package_lpa', 'min_cgpa', 'max_backlogs', 'min_attendance'].some(key => !Number.isFinite(body[key]))) {
      setError('Numeric drive fields must contain finite numbers.'); return;
    }
    if (!body.company || !body.role || !body.drive_date || body.package_lpa <= 0 || body.min_cgpa < 0 || body.min_cgpa > 10 || body.max_backlogs < 0 || !Number.isInteger(body.max_backlogs) || body.min_attendance < 0 || body.min_attendance > 100) {
      setError('Company, role and date are required. Package must be positive; CGPA 0–10, attendance 0–100 and backlogs a non-negative integer.'); return;
    }
    await perform(async () => { await api.savePlacementDrive(token, editing, body); setEditing(null); setForm({ ...emptyDrive }); await refresh(); setSuccess('Drive saved.'); });
  }
  function editDrive(drive) {
    setEditing(drive.id); setForm(Object.fromEntries(Object.keys(emptyDrive).map(key => [key, drive[key] ?? emptyDrive[key]])));
  }
  async function lifecycle(drive, action) {
    if (!window.confirm(`${action === 'close' ? 'Close' : 'Cancel'} ${drive.company} drive?`)) return;
    await perform(async () => { await api.placementAction(token, drive.id, action); await refresh(); setSuccess('Drive status updated.'); });
  }
  async function generate(drive) {
    const regenerate = drive.status === 'SHORTLIST_GENERATED' || drive.candidate_count > 0;
    if (regenerate && !window.confirm('Regenerate shortlist? Existing eligibility will be recalculated, including finalized candidates. Recorded outcomes will remain unchanged.')) return;
    await perform(async () => { await api.placementAction(token, drive.id, 'shortlist', { regenerate }); await refresh(); await loadDetail(drive.id); setSuccess('Shortlist generated.'); window.dispatchEvent(new Event('mawos:notifications-changed')); });
  }
  async function saveOutcome(event) {
    event.preventDefault();
    await perform(async () => { await api.savePlacementOutcome(token, selected, outcome.usn.trim().toUpperCase(), { outcome_status: outcome.outcome_status, package_offered: outcome.package_offered === '' ? null : Number(outcome.package_offered), allow_multiple_offers: outcome.allow_multiple_offers }); await loadDetail(selected); setSuccess('Outcome saved.'); });
  }
  return <><PageHeader title="Placement management" eyebrow="MAWOS / Careers"><p>Manage recruitment drives, shortlists and offers.</p></PageHeader>
    {error && <div role="alert" className="mb-4 rounded border border-red-200 bg-red-50 p-3">{error} <button className="btn-secondary" onClick={() => setRetry(retry + 1)}>Retry list</button></div>}
    {success && <p role="status" className="mb-4 text-green-700">{success}</p>}
    {busy && <p role="status">Working…</p>}
    <DashboardCard title={editing ? 'Edit drive' : 'Create drive'}>
      <form onSubmit={saveDrive}><fieldset disabled={busy} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {[['company', 'Company', 'text'], ['role', 'Role', 'text'], ['package_lpa', 'Package (LPA)', 'number'], ['drive_date', 'Drive date', 'date'], ['departments', 'Departments', 'text'], ['min_cgpa', 'Minimum CGPA', 'number'], ['max_backlogs', 'Maximum backlogs', 'number'], ['min_attendance', 'Minimum attendance (%)', 'number'], ['application_deadline', 'Application deadline', 'date']].map(([name, label, type]) =>
          <label key={name} className="text-sm">{label}<input className="input mt-1 w-full rounded border p-2" aria-label={label} type={type} value={form[name]} required={name !== 'application_deadline'} maxLength={['company', 'role'].includes(name) ? 128 : name === 'departments' ? 64 : undefined} min={type === 'number' ? name === 'package_lpa' ? '0.01' : '0' : undefined} max={name === 'min_cgpa' ? 10 : name === 'min_attendance' ? 100 : undefined} step={name === 'max_backlogs' ? 1 : type === 'number' ? 'any' : undefined} onChange={event => setForm({ ...form, [name]: event.target.value })} /></label>)}
        <label className="text-sm">Initial status<select className="mt-1 w-full rounded border p-2" value={form.status} onChange={event => setForm({ ...form, status: event.target.value })}><option>OPEN</option>{(!editing || drives?.find(d => d.id === editing)?.status === 'DRAFT') && <option>DRAFT</option>}</select></label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={form.requires_fee_clearance} onChange={event => setForm({ ...form, requires_fee_clearance: event.target.checked })} />Require fee clearance</label>
        <div className="flex items-end gap-2"><button className="btn-primary" type="submit">{editing ? 'Save drive' : 'Create drive'}</button>{editing && <button className="btn-secondary" type="button" onClick={() => { setEditing(null); setForm({ ...emptyDrive }); }}>Cancel edit</button>}</div>
      </fieldset></form>
    </DashboardCard>
    <div className="my-4"><DashboardCard title="Placement drives">
      {!drives ? <p>Loading drives…</p> : !drives.length ? <p>No placement drives available.</p> : <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr>{['Company / role', 'Package / date', 'Departments', 'Status', 'Candidates / shortlisted', 'Actions'].map(label => <th className="p-2" key={label}>{label}</th>)}</tr></thead><tbody>{drives.map(drive => <tr key={drive.id} className="border-t">
        <td className="p-2">{drive.company}<br />{drive.role}</td><td className="p-2">₹{drive.package_lpa} LPA<br />{drive.drive_date}</td><td className="p-2">{drive.departments}</td><td className="p-2"><span className={badge}>{drive.status}</span></td><td className="p-2">{drive.candidate_count ?? '—'} / {drive.shortlisted_count ?? '—'}</td>
        <td className="min-w-48 p-2"><div className="flex flex-wrap gap-2">
          {editable.includes(drive.status) && <button disabled={busy} className="btn-secondary" onClick={() => editDrive(drive)}>Edit</button>}
          {active.includes(drive.status) && <><button disabled={busy} className="btn-primary" onClick={() => generate(drive)}>{drive.status === 'SHORTLIST_GENERATED' || drive.candidate_count > 0 ? 'Regenerate shortlist' : 'Generate shortlist'}</button><button disabled={busy} className="btn-secondary" onClick={() => lifecycle(drive, 'close')}>Close</button></>}
          {drive.status !== 'CANCELLED' && <button disabled={busy} className="btn-secondary" onClick={() => lifecycle(drive, 'cancel')}>Cancel drive</button>}
          <button disabled={busy} className="btn-secondary" onClick={() => perform(() => loadDetail(drive.id))}>View shortlist / outcomes</button>
        </div></td>
      </tr>)}</tbody></table></div>}
    </DashboardCard></div>
    {selected != null && <DashboardCard title={`Shortlist · ${drives?.find(d => d.id === selected)?.company || selected}`}>
      {!shortlist.length ? <p>No candidates evaluated.</p> : <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr><th className="p-2">Student</th><th className="p-2">Eligibility / score / reasons</th></tr></thead><tbody>{shortlist.map(entry => <tr key={entry.usn} className="border-t"><td className="p-2">{entry.usn}<br />{entry.name}</td><td className="p-2"><Evaluation entry={entry} admin /></td></tr>)}</tbody></table></div>}
      <h3 className="my-3 font-semibold">Record outcome</h3><form onSubmit={saveOutcome}><fieldset disabled={busy} className="grid gap-3 sm:grid-cols-2">
        <label>Student USN<input required maxLength={16} className="block w-full rounded border p-2" value={outcome.usn} onChange={event => setOutcome({ ...outcome, usn: event.target.value })} /></label>
        <label>Outcome status<select className="block w-full rounded border p-2" value={outcome.outcome_status} onChange={event => setOutcome({ ...outcome, outcome_status: event.target.value })}>{['OFFER_MADE', 'OFFER_ACCEPTED', 'OFFER_DECLINED', 'REJECTED'].map(status => <option key={status}>{status}</option>)}</select></label>
        <label>Package offered (LPA)<input type="number" min="0.01" step="any" className="block w-full rounded border p-2" value={outcome.package_offered} onChange={event => setOutcome({ ...outcome, package_offered: event.target.value })} /></label>
        <label><input type="checkbox" checked={outcome.allow_multiple_offers} onChange={event => setOutcome({ ...outcome, allow_multiple_offers: event.target.checked })} /> Allow multiple accepted offers</label>
        <button className="btn-primary" type="submit">Save outcome</button>
      </fieldset></form>
      <h3 className="my-3 font-semibold">Recorded outcomes</h3>{!outcomes.length ? <p>No outcomes recorded.</p> : <ul className="space-y-2">{outcomes.map(row => <li key={row.usn}>{row.usn} · <span className={badge}>{row.outcome_status}</span> · {row.package_offered == null ? 'Package not recorded' : `₹${row.package_offered} LPA`}</li>)}</ul>}
    </DashboardCard>}
  </>;
}
