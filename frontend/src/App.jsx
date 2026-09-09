import { Component } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { AppLayout } from './layouts/AppLayout';
import { ProtectedRoute, RoleRoute } from './components/routes';
import LoginPage from './pages/auth/LoginPage';
import { ForbiddenPage, NotFoundPage } from './pages/errors';
import StudentDashboard from './pages/student/StudentDashboard';
import StudentTimetable from './pages/student/StudentTimetable';
import FacultyDashboard from './pages/faculty/FacultyDashboard';
import FacultyTimetable from './pages/faculty/FacultyTimetable';
import { HodTimetable, AdminTimetable, PrincipalTimetable } from './pages/timetable/Timetable';
import HodDashboard from './pages/hod/HodDashboard';
import PrincipalDashboard from './pages/principal/PrincipalDashboard';
import AdminDashboard from './pages/admin/AdminDashboard';
import AssistantPage from './pages/shared/AssistantPage';
import SystemPage from './pages/shared/SystemPage';
import { StudentScholarships, FacultyScholarships, HodScholarships } from './pages/shared/Scholarships';
import { useAuth } from './context/AuthContext';
import { defaultRouteByRole } from './routes/roleRoutes';

function HomeRedirect() { const { user } = useAuth(); return <Navigate to={defaultRouteByRole[user?.role] || '/login'} replace />; }
class ErrorBoundary extends Component { state = { error: null }; static getDerivedStateFromError(error) { return { error }; } render() { return this.state.error ? <main className="p-8"><h1 className="text-2xl font-bold">Something went wrong</h1><p className="mt-2 text-muted">{this.state.error.message}</p><button className="btn-primary mt-5" onClick={() => window.location.assign('/')}>Reload MAWOS</button></main> : this.props.children; } }
export default function App() { return <ErrorBoundary><Routes><Route path="/login" element={<LoginPage />} /><Route element={<ProtectedRoute />}><Route element={<AppLayout />}><Route index element={<HomeRedirect />} /><Route element={<RoleRoute roles={['student']} />}><Route path="student" element={<StudentDashboard />} /><Route path="student/timetable" element={<StudentTimetable />} /><Route path="student/scholarships" element={<StudentScholarships />} /></Route><Route element={<RoleRoute roles={['faculty', 'hod']} />}><Route path="faculty" element={<FacultyDashboard />} /><Route path="faculty/timetable" element={<FacultyTimetable />} /></Route><Route element={<RoleRoute roles={['faculty']} />}><Route path="faculty/scholarships" element={<FacultyScholarships />} /></Route><Route element={<RoleRoute roles={['hod']} />}><Route path="hod" element={<HodDashboard />} /><Route path="hod/timetable" element={<HodTimetable />} /><Route path="hod/scholarships" element={<HodScholarships />} /></Route><Route element={<RoleRoute roles={['principal', 'admin']} />}><Route path="principal" element={<PrincipalDashboard />} /><Route path="principal/timetable" element={<PrincipalTimetable />} /></Route><Route element={<RoleRoute roles={['admin']} />}><Route path="admin" element={<AdminDashboard />} /><Route path="admin/timetable" element={<AdminTimetable />} /></Route><Route path="assistant" element={<AssistantPage />} /><Route path="system" element={<SystemPage />} /><Route path="forbidden" element={<ForbiddenPage />} /><Route path="*" element={<NotFoundPage />} /></Route></Route><Route path="*" element={<Navigate to="/login" replace />} /></Routes></ErrorBoundary>; }
