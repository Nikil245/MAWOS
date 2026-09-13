import { useState } from 'react';
import { LockKeyhole, ShieldCheck } from 'lucide-react';
import { Navigate, useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { defaultRouteByRole } from '../../routes/roleRoutes';

export default function ChangePasswordPage() {
  const { user, changePassword, logout } = useAuth();
  const navigate = useNavigate();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  if (!user?.must_change_password) return <Navigate to={defaultRouteByRole[user?.role] || '/login'} replace />;
  const submit = async (event) => {
    event.preventDefault(); setError('');
    if (next.length < 10) return setError('Use at least 10 characters.');
    if (next !== confirm) return setError('New passwords do not match.');
    setBusy(true);
    try { const changed = await changePassword(current, next); navigate(defaultRouteByRole[changed.role], { replace: true }); }
    catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };
  return <main className="grid min-h-screen place-items-center bg-canvas p-5"><form className="w-full max-w-md rounded-2xl border bg-white p-7 shadow-card" onSubmit={submit}><span className="grid h-11 w-11 place-items-center rounded-xl bg-blue-50 text-primary"><LockKeyhole /></span><h1 className="mt-5 text-2xl font-bold">Create your private password</h1><p className="mt-2 text-sm text-muted">Your administrator-issued password is temporary. Change it before viewing parent portal data.</p><label className="label mt-6" htmlFor="current-password">Temporary password</label><input id="current-password" className="field" type="password" autoComplete="current-password" value={current} onChange={e => setCurrent(e.target.value)} required /><label className="label mt-4" htmlFor="new-password">New password</label><input id="new-password" className="field" type="password" autoComplete="new-password" value={next} onChange={e => setNext(e.target.value)} minLength={10} maxLength={128} required /><label className="label mt-4" htmlFor="confirm-password">Confirm new password</label><input id="confirm-password" className="field" type="password" autoComplete="new-password" value={confirm} onChange={e => setConfirm(e.target.value)} required />{error && <p role="alert" className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p>}<button className="btn-primary mt-6 w-full" disabled={busy}>{busy ? 'Changing password…' : 'Change password and continue'}</button><button type="button" className="mt-4 w-full text-sm text-muted hover:text-slate-900" onClick={logout}>Sign out</button><p className="mt-5 text-center text-xs text-muted"><ShieldCheck className="mr-1 inline" size={14} />The temporary password is never stored in plaintext.</p></form></main>;
}
