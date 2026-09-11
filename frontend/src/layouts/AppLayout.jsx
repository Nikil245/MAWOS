import { useCallback, useEffect, useRef, useState } from 'react';
import { Bell, Bot, Building2, CalendarDays, ChevronDown, ClipboardCheck, GraduationCap, LayoutDashboard, LogOut, Menu, Settings, ShieldCheck, Users, X, WalletCards } from 'lucide-react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { NotificationDrawer } from '../components/ui';
import { ApiError, api } from '../services/api';

export const navigationByRole = {
  student: [{ to: '/student/placements', icon: Building2, label: 'My placements' }, { to: '/student', icon: LayoutDashboard, label: 'Overview' }, { to: '/student/scholarships', icon: WalletCards, label: 'Scholarships' }, { to: '/student/timetable', icon: CalendarDays, label: 'Timetable' }, { to: '/assistant', icon: Bot, label: 'Academic assistant' }, { to: '/system', icon: ShieldCheck, label: 'System' }],
  faculty: [{ to: '/faculty', icon: ClipboardCheck, label: 'My classes' }, { to: '/faculty/scholarships', icon: WalletCards, label: 'Scholarship Management' }, { to: '/faculty/timetable', icon: CalendarDays, label: 'My timetable' }, { to: '/assistant', icon: Bot, label: 'Academic assistant' }, { to: '/system', icon: ShieldCheck, label: 'System' }],
  hod: [{ to: '/hod/timetable', icon: CalendarDays, label: 'Timetable planning' }, { to: '/hod', icon: Building2, label: 'Department' }, { to: '/hod/scholarships', icon: WalletCards, label: 'Scholarship Approvals' }, { to: '/faculty', icon: ClipboardCheck, label: 'My classes' }, { to: '/assistant', icon: Bot, label: 'Academic assistant' }, { to: '/system', icon: ShieldCheck, label: 'System' }],
  principal: [{ to: '/principal/timetable', icon: CalendarDays, label: 'Timetable coverage' }, { to: '/principal', icon: GraduationCap, label: 'Institution' }, { to: '/assistant', icon: Bot, label: 'Academic assistant' }, { to: '/system', icon: ShieldCheck, label: 'System' }],
  admin: [{ to: '/admin/placements', icon: Building2, label: 'Placement management' }, { to: '/admin/timetable', icon: CalendarDays, label: 'Timetable configuration' }, { to: '/admin', icon: Users, label: 'Admissions' }, { to: '/principal', icon: GraduationCap, label: 'Institution' }, { to: '/system', icon: ShieldCheck, label: 'System monitoring' }, { to: '/assistant', icon: Bot, label: 'Academic assistant' }],
};

const pageTitle = (pathname) => ({ '/student/placements': 'My placements', '/admin/placements': 'Placement management', '/hod/timetable': 'Timetable planning', '/admin/timetable': 'Timetable configuration', '/principal/timetable': 'Timetable coverage', '/student': 'Student overview', '/student/timetable': 'My timetable', '/student/scholarships': 'Scholarships', '/faculty': 'Faculty workspace', '/faculty/timetable': 'My timetable', '/faculty/scholarships': 'Scholarship Management', '/hod': 'Department overview', '/hod/scholarships': 'Scholarship Approvals', '/principal': 'Institution overview', '/admin': 'Admissions administration', '/assistant': 'Academic assistant', '/system': 'System monitoring' }[pathname] || 'MAWOS');

export function AppLayout() {
  const { token, user, logout } = useAuth(); const [drawer, setDrawer] = useState(false); const [noticeOpen, setNoticeOpen] = useState(false); const [profileOpen, setProfileOpen] = useState(false); const [notifications, setNotifications] = useState([]); const [notificationState, setNotificationState] = useState('loading'); const [notificationError, setNotificationError] = useState(null); const requestRef = useRef(0); const abortRef = useRef(null); const location = useLocation(); const navigate = useNavigate();
  const accountKey = `${token || ''}:${user?.username || ''}`;
  const refreshNotifications = useCallback(async () => {
    if (!token || !user) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const requestId = ++requestRef.current;
    setNotificationState('loading'); setNotificationError(null);
    try {
      const result = await api.notifications(token, controller.signal);
      if (requestId !== requestRef.current) return;
      setNotifications(result.notifications || []);
      setNotificationState('ready');
    } catch (error) {
      if (controller.signal.aborted || requestId !== requestRef.current) return;
      setNotifications([]); setNotificationError(error);
      setNotificationState(error instanceof ApiError && error.status === 401 ? 'expired' : 'error');
    }
  }, [token, user]);
  useEffect(() => {
    requestRef.current += 1; abortRef.current?.abort(); setNotifications([]); setNotificationError(null);
    if (!token || !user) { setNotificationState('ready'); return undefined; }
    refreshNotifications();
    return () => abortRef.current?.abort();
  }, [accountKey, refreshNotifications, token, user]);
  useEffect(() => {
    window.addEventListener('mawos:notifications-changed', refreshNotifications);
    return () => window.removeEventListener('mawos:notifications-changed', refreshNotifications);
  }, [refreshNotifications]);
  if (!user) return null;
  const items = navigationByRole[user.role] || [];
  const signOut = () => { logout(); navigate('/login', { replace: true }); };
  const sidebar = <><div className="flex h-16 items-center gap-3 border-b border-slate-700 px-5"><span className="grid h-9 w-9 place-items-center rounded-lg bg-primary font-bold text-white">M</span><div><p className="font-bold tracking-wide text-white">MAWOS</p><p className="text-[11px] text-slate-400">University ERP</p></div></div><nav className="flex-1 space-y-1 p-3">{items.map(({ to, icon: Icon, label }) => <NavLink key={to} to={to} end={to === '/student' || to === '/faculty'} onClick={() => setDrawer(false)} className={({ isActive }) => `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium ${isActive ? 'bg-primary text-white' : 'text-slate-300 hover:bg-slate-800 hover:text-white'}`}><Icon size={18} />{label}</NavLink>)}</nav><div className="border-t border-slate-700 p-3"><button className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-slate-300 hover:bg-slate-800" onClick={() => alert('Settings are not yet exposed by the current API.')}><Settings size={18} />Settings</button><button className="mt-1 flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-slate-300 hover:bg-slate-800" onClick={signOut}><LogOut size={18} />Logout</button></div></>;
  const unreadCount = notifications.filter((notice) => !notice.read).length;
  const markRead = async (id) => { const previous = notifications; const notice = notifications.find(item => item.id === id); if (!notice || notice.read) return; setNotifications(items => items.map(item => item.id === id ? { ...item, read: true } : item)); try { await api.markNotificationRead(token, id); } catch (error) { setNotifications(previous); setNotificationError(error); } };
  const markAllRead = async () => { const previous = notifications; if (!previous.some(item => !item.read)) return; setNotifications(items => items.map(item => ({ ...item, read: true }))); try { await api.markAllNotificationsRead(token); } catch (error) { setNotifications(previous); setNotificationError(error); } };
  const openNotifications = () => { setNoticeOpen(true); if (notificationState !== 'loading') refreshNotifications(); };
  return <div className="min-h-screen bg-canvas lg:grid lg:grid-cols-[256px_1fr]"><aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col bg-navy lg:flex">{sidebar}</aside>{drawer && <div className="fixed inset-0 z-40 lg:hidden"><button className="absolute inset-0 bg-slate-950/40" onClick={() => setDrawer(false)} aria-label="Close menu overlay" /><aside className="relative flex h-full w-72 flex-col bg-navy"><button className="absolute right-3 top-5 text-white" onClick={() => setDrawer(false)} aria-label="Close menu"><X /></button>{sidebar}</aside></div>}<div className="lg:col-start-2"><header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b bg-white/95 px-4 backdrop-blur sm:px-6"><div className="flex items-center gap-3"><button className="lg:hidden" onClick={() => setDrawer(true)} aria-label="Open navigation"><Menu /></button><div><p className="text-xs text-muted">MAWOS / {user.role}</p><h1 className="font-semibold">{pageTitle(location.pathname)}</h1></div></div><div className="flex items-center gap-3"><span className="hidden text-xs text-muted sm:block">Updated just now</span><button className="relative rounded-lg p-2 hover:bg-slate-100" aria-label={`Open notifications${unreadCount ? ` (${unreadCount} unread)` : ''}`} onClick={openNotifications}><Bell size={19} />{unreadCount > 0 && <span className="absolute right-1.5 top-1.5 min-w-4 rounded-full bg-primary px-1 text-center text-[10px] font-bold text-white">{unreadCount}</span>}</button><div className="relative"><button className="flex items-center gap-2 rounded-lg p-1.5 hover:bg-slate-100" onClick={() => setProfileOpen(!profileOpen)} aria-expanded={profileOpen}><span className="grid h-8 w-8 place-items-center rounded-full bg-teal text-sm font-bold text-white">{user.name?.slice(0, 1)}</span><span className="hidden text-left sm:block"><span className="block max-w-36 truncate text-sm font-semibold">{user.name}</span><span className="block text-xs capitalize text-muted">{user.role}</span></span><ChevronDown size={15} /></button>{profileOpen && <div className="absolute right-0 mt-2 w-44 rounded-lg border bg-white p-1 shadow-lg"><button className="w-full rounded-md px-3 py-2 text-left text-sm hover:bg-slate-50" onClick={signOut}>Sign out</button></div>}</div></div></header><main className="mx-auto max-w-7xl p-4 sm:p-6"><Outlet /></main></div><NotificationDrawer open={noticeOpen} onClose={() => setNoticeOpen(false)} notifications={notifications} loading={notificationState === 'loading'} error={notificationError} sessionExpired={notificationState === 'expired'} retry={refreshNotifications} onMarkRead={markRead} onMarkAllRead={markAllRead} /></div>;
}
