import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { LoadingSkeleton } from './ui';
import { roleMismatchDestination } from '../routes/roleRoutes';

export function ProtectedRoute() { const { token, checking } = useAuth(); const location = useLocation(); if (checking) return <div className="p-8"><LoadingSkeleton /></div>; return token ? <Outlet /> : <Navigate to="/login" state={{ from: location }} replace />; }
export function RoleRoute({ roles }) { const { user } = useAuth(); const location = useLocation(); return roles.includes(user?.role) ? <Outlet /> : <Navigate to={roleMismatchDestination(user?.role, location.pathname)} replace />; }
