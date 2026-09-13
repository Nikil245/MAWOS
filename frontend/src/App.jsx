import { StudentLibrary, LibrarySlip, LibrarianLibrary, LibrarianManagement } from './pages/library/Library';
import { Component } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppLayout } from "./layouts/AppLayout";
import { ProtectedRoute, RoleRoute } from "./components/routes";
import LoginPage from "./pages/auth/LoginPage";
import ChangePasswordPage from "./pages/auth/ChangePasswordPage";
import { ForbiddenPage, NotFoundPage } from "./pages/errors";
import StudentDashboard from "./pages/student/StudentDashboard";
import StudentTimetable from "./pages/student/StudentTimetable";
import FacultyDashboard from "./pages/faculty/FacultyDashboard";
import FacultyTimetable from "./pages/faculty/FacultyTimetable";
import {
  HodTimetable,
  AdminTimetable,
  PrincipalTimetable,
} from "./pages/timetable/Timetable";
import HodDashboard from "./pages/hod/HodDashboard";
import PrincipalDashboard from "./pages/principal/PrincipalDashboard";
import AdminDashboard from "./pages/admin/AdminDashboard";
import AssistantPage from "./pages/shared/AssistantPage";
import SystemPage from "./pages/shared/SystemPage";
import {
  StudentScholarships,
  FacultyScholarships,
  HodScholarships,
} from "./pages/shared/Scholarships";
import {
  AdminPlacementDetail,
  AdminPlacements,
  StudentPlacementDetail,
  StudentPlacements,
} from "./pages/placement/Placements";
import { useAuth } from "./context/AuthContext";
import { defaultRouteByRole } from "./routes/roleRoutes";
import { AdminCampusEvents, EventDetail, EventsList } from "./pages/shared/CampusEvents";
import ParentManagement from "./pages/admin/ParentManagement";
import { ParentDashboard, ParentNotifications, ParentTimetable } from "./pages/parent/ParentPortal";

function HomeRedirect() {
  const { user } = useAuth();
  return <Navigate to={defaultRouteByRole[user?.role] || "/login"} replace />;
}
class ErrorBoundary extends Component {
  state = { error: null };
  static getDerivedStateFromError(error) {
    return { error };
  }
  render() {
    return this.state.error ? (
      <main className="p-8">
        <h1 className="text-2xl font-bold">Something went wrong</h1>
        <p className="mt-2 text-muted">{this.state.error.message}</p>
        <button
          className="btn-primary mt-5"
          onClick={() => window.location.assign("/")}
        >
          Reload MAWOS
        </button>
      </main>
    ) : (
      this.props.children
    );
  }
}
export default function App() {
  return (
    <ErrorBoundary>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<ProtectedRoute />}>
          <Route path="change-password" element={<ChangePasswordPage />} />
          <Route element={<AppLayout />}>
            <Route index element={<HomeRedirect />} />
            <Route element={<RoleRoute roles={["student"]} />}>
              <Route path="student" element={<StudentDashboard />} />
              <Route path="student/library" element={<StudentLibrary />} />
              <Route path="student/library/slips/:reservationId" element={<LibrarySlip />} />
              <Route
                path="student/placements"
                element={<StudentPlacements />}
              />
              <Route
                path="student/placements/:driveId"
                element={<StudentPlacementDetail />}
              />
              <Route path="student/timetable" element={<StudentTimetable />} />
              <Route
                path="student/scholarships"
                element={<StudentScholarships />}
              />
            </Route>
            <Route element={<RoleRoute roles={["faculty", "hod"]} />}>
              <Route path="faculty" element={<FacultyDashboard />} />
              <Route path="faculty/timetable" element={<FacultyTimetable />} />
            </Route>
            <Route element={<RoleRoute roles={["faculty"]} />}>
              <Route
                path="faculty/scholarships"
                element={<FacultyScholarships />}
              />
            </Route>
            <Route element={<RoleRoute roles={["hod"]} />}>
              <Route path="hod" element={<HodDashboard />} />
              <Route path="hod/timetable" element={<HodTimetable />} />
              <Route path="hod/scholarships" element={<HodScholarships />} />
            </Route>
            <Route element={<RoleRoute roles={["principal", "admin"]} />}>
              <Route path="principal" element={<PrincipalDashboard />} />
              <Route
                path="principal/timetable"
                element={<PrincipalTimetable />}
              />
            </Route>
            <Route element={<RoleRoute roles={["admin"]} />}>
              <Route path="admin" element={<AdminDashboard />} />
              <Route path="admin/library" element={<LibrarianLibrary />} />
              <Route path="admin/librarians" element={<LibrarianManagement />} />
              <Route path="admin/placements" element={<AdminPlacements />} />
              <Route
                path="admin/placements/:driveId"
                element={<AdminPlacementDetail />}
              />
              <Route path="admin/timetable" element={<AdminTimetable />} />
              <Route path="admin/events" element={<AdminCampusEvents />} />
              <Route path="admin/parents" element={<ParentManagement />} />
            </Route>
            <Route element={<RoleRoute roles={["librarian"]} />}>
              <Route path="librarian/library" element={<LibrarianLibrary />} />
            </Route>
            <Route element={<RoleRoute roles={["parent"]} />}>
              <Route path="parent" element={<ParentDashboard />} />
              <Route path="parent/timetable" element={<ParentTimetable />} />
              <Route path="parent/notifications" element={<ParentNotifications />} />
              <Route path="parent/events/:eventId" element={<EventDetail />} />
            </Route>
            <Route element={<RoleRoute roles={["student", "faculty", "hod", "principal", "admin"]} />}>
              <Route path="events" element={<EventsList />} />
              <Route path="events/:eventId" element={<EventDetail />} />
              <Route path="assistant" element={<AssistantPage />} />
              <Route path="system" element={<SystemPage />} />
            </Route>
            <Route path="forbidden" element={<ForbiddenPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    </ErrorBoundary>
  );
}
