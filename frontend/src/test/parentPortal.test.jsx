import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ParentManagement from '../pages/admin/ParentManagement';
import { ParentDashboard } from '../pages/parent/ParentPortal';
import { isRouteAllowedForRole, roleMismatchDestination } from '../routes/roleRoutes';

const mocks = vi.hoisted(() => ({
  parentProfile: vi.fn(), parentDashboard: vi.fn(), adminParents: vi.fn(),
  searchStudents: vi.fn(), createParent: vi.fn(), setParentActive: vi.fn(),
  setParentStudentActive: vi.fn(), updateParent: vi.fn(), addParentStudent: vi.fn(),
}));
vi.mock('../context/AuthContext', () => ({ useAuth: () => ({ token: 'parent-token', user: { role: 'parent' } }) }));
vi.mock('../services/api', () => ({ api: mocks }));

const children = [
  { usn: '4MT23AI001', name: 'Asha', department: 'AIML', year: 3, semester: 5, section: 'A' },
  { usn: '4MT23AI002', name: 'Bina', department: 'AIML', year: 3, semester: 5, section: 'A' },
];
const dashboard = usn => ({ child: children.find(child => child.usn === usn), attendance: { overall: usn.endsWith('1') ? 91 : 72, warning: usn.endsWith('2'), subjects: [] }, marks: { average: null, subjects: [] }, fees: { total_outstanding: 0, items: [] }, exams: { eligible: null, hall_ticket_status: 'unavailable', reasons: null, next: [] }, timetable: { today: [], message: 'No classes today.' }, scholarship: { state: 'NONE', total_available: 0, opportunity: null }, placements: [], events: [] });

describe('parent portal UI', () => {
  beforeEach(() => { localStorage.clear(); Object.values(mocks).forEach(mock => mock.mockReset()); });

  it('switches linked children, updates API calls, and remains read-only', async () => {
    mocks.parentProfile.mockResolvedValue({ name: 'Guardian', children });
    mocks.parentDashboard.mockImplementation((_token, usn) => Promise.resolve(dashboard(usn)));
    render(<MemoryRouter><ParentDashboard /></MemoryRouter>);
    expect(await screen.findByText('91%')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Select linked student'), { target: { value: '4MT23AI002' } });
    expect(await screen.findByText('72%')).toBeInTheDocument();
    expect(mocks.parentDashboard).toHaveBeenLastCalledWith('parent-token', '4MT23AI002');
    expect(localStorage.getItem('mawos_role_parent_child')).toBe('4MT23AI002');
    expect(screen.queryByRole('button', { name: /pay|edit|delete/i })).not.toBeInTheDocument();
  });

  it('renders honest empty and unauthorized retry states', async () => {
    mocks.parentProfile.mockResolvedValue({ name: 'Guardian', children: [] });
    const view = render(<MemoryRouter><ParentDashboard /></MemoryRouter>);
    expect(await screen.findByText('No linked students')).toBeInTheDocument();
    view.unmount();
    mocks.parentProfile.mockResolvedValue({ name: 'Guardian', children: [children[0]] });
    mocks.parentDashboard.mockRejectedValue(Object.assign(new Error('Student is not linked to this parent'), { status: 403 }));
    render(<MemoryRouter><ParentDashboard /></MemoryRouter>);
    expect(await screen.findByText('Student is not linked to this parent')).toBeInTheDocument();
  });

  it('shows generated credentials once after admin creation with copy support', async () => {
    mocks.adminParents.mockResolvedValue({ parents: [] });
    mocks.searchStudents.mockResolvedValue({ students: [children[0]] });
    mocks.createParent.mockResolvedValue({ generated_credentials: { username: 'asha.parent', temporary_password: 'Temp-parent-1!' } });
    render(<MemoryRouter><ParentManagement /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText('Search students'), { target: { value: 'Asha' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Select' }));
    fireEvent.change(screen.getByLabelText(/Full name/), { target: { value: 'Asha Parent' } });
    fireEvent.change(screen.getByLabelText(/Username/), { target: { value: 'asha.parent' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create parent account' }));
    expect(await screen.findByText('Copy these credentials now')).toBeInTheDocument();
    expect(screen.getByText(/Temp-parent-1!/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy credentials' })).toBeInTheDocument();
  });

  it('protects parent routes from other roles and other portals from parents', () => {
    expect(isRouteAllowedForRole('parent', '/parent')).toBe(true);
    expect(isRouteAllowedForRole('parent', '/student')).toBe(false);
    expect(isRouteAllowedForRole('student', '/parent')).toBe(false);
    expect(roleMismatchDestination('parent', '/admin')).toBe('/parent');
  });
});
