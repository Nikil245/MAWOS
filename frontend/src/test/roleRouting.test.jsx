import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RoleRoute } from '../components/routes';
import { FacultyScholarships } from '../pages/shared/Scholarships';
import { navigationByRole } from '../layouts/AppLayout';
import { isRouteAllowedForRole, safeReturnPath } from '../routes/roleRoutes';

const mocks = vi.hoisted(() => ({ scholarships: vi.fn() }));
let auth = { user: { role: 'hod', dept: 'AIML' }, token: 'hod-token' };
vi.mock('../context/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('../services/api', () => ({ api: { scholarships: mocks.scholarships } }));

function Location() { return <output data-testid="location">{useLocation().pathname}</output>; }
function HodPage() { return <><Location /><p>HOD approvals</p></>; }
function StudentPage() { return <><Location /><p>Student scholarships</p></>; }
function FacultyPage() { return <><Location /><p>Faculty scholarship management</p></>; }

function ScholarshipRoutes({ facultyComponent = <FacultyPage /> }) {
  return <MemoryRouter initialEntries={['/faculty/scholarships']}><Routes>
    <Route element={<RoleRoute roles={['faculty']} />}><Route path="/faculty/scholarships" element={facultyComponent} /></Route>
    <Route element={<RoleRoute roles={['hod']} />}><Route path="/hod/scholarships" element={<HodPage />} /></Route>
    <Route element={<RoleRoute roles={['student']} />}><Route path="/student/scholarships" element={<StudentPage />} /></Route>
  </Routes></MemoryRouter>;
}

describe('role-aware scholarship routing', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('redirects an HOD directly (including a refresh) before the faculty component or API can run', async () => {
    auth = { user: { role: 'hod', dept: 'AIML' }, token: 'hod-token' };
    render(<ScholarshipRoutes facultyComponent={<FacultyScholarships />} />);
    expect(await screen.findByText('HOD approvals')).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent('/hod/scholarships');
    expect(screen.queryByText('Scholarship Management')).not.toBeInTheDocument();
    expect(mocks.scholarships).not.toHaveBeenCalled();
  });

  it('redirects faculty away from HOD approvals', async () => {
    auth = { user: { role: 'faculty', dept: 'AIML' }, token: 'faculty-token' };
    render(<MemoryRouter initialEntries={['/hod/scholarships']}><Routes>
      <Route element={<RoleRoute roles={['faculty']} />}><Route path="/faculty/scholarships" element={<FacultyPage />} /></Route>
      <Route element={<RoleRoute roles={['hod']} />}><Route path="/hod/scholarships" element={<HodPage />} /></Route>
    </Routes></MemoryRouter>);
    expect(await screen.findByText('Faculty scholarship management')).toBeInTheDocument();
  });

  it('redirects students away from either staff scholarship route', async () => {
    auth = { user: { role: 'student' }, token: 'student-token' };
    render(<ScholarshipRoutes />);
    expect(await screen.findByText('Student scholarships')).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent('/student/scholarships');
  });

  it('does not retain a faculty scholarship page after an account role switch', async () => {
    auth = { user: { role: 'faculty' }, token: 'faculty-token' };
    const view = render(<ScholarshipRoutes />);
    expect(await screen.findByText('Faculty scholarship management')).toBeInTheDocument();
    auth = { user: { role: 'hod' }, token: 'hod-token' };
    view.rerender(<ScholarshipRoutes />);
    expect(await screen.findByText('HOD approvals')).toBeInTheDocument();
  });

  it('only accepts return URLs that belong to the authenticated role', () => {
    expect(safeReturnPath('faculty', { pathname: '/faculty/scholarships', search: '?draft=1' })).toBe('/faculty/scholarships?draft=1');
    expect(safeReturnPath('hod', { pathname: '/faculty/scholarships' })).toBeNull();
    expect(safeReturnPath('student', { pathname: '//evil.example' })).toBeNull();
    expect(isRouteAllowedForRole('faculty', '/hod/scholarships')).toBe(false);
  });

  it('exposes role-correct scholarship sidebar links and labels', () => {
    expect(navigationByRole.student).toContainEqual(expect.objectContaining({ to: '/student/scholarships', label: 'Scholarships' }));
    expect(navigationByRole.faculty).toContainEqual(expect.objectContaining({ to: '/faculty/scholarships', label: 'Scholarship Management' }));
    expect(navigationByRole.hod).toContainEqual(expect.objectContaining({ to: '/hod/scholarships', label: 'Scholarship Approvals' }));
  });
});
