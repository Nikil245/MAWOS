import { render, screen, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../context/AuthContext';
import AdminDashboard from '../pages/admin/AdminDashboard';
import PrincipalDashboard from '../pages/principal/PrincipalDashboard';
import { api } from '../services/api';

vi.mock('../services/api', () => ({
  api: { principalAnalytics: vi.fn(), admissions: vi.fn(), adminAction: vi.fn(), campusEvents: vi.fn() },
  setUnauthorizedHandler: vi.fn(),
  ApiError: class ApiError extends Error {},
}));
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }) => <div>{children}</div>,
  BarChart: ({ children }) => <div>{children}</div>,
  Bar: ({ children }) => <div>{children}</div>,
  LineChart: ({ children }) => <div>{children}</div>,
  Line: () => null,
  PieChart: ({ children }) => <div>{children}</div>,
  Pie: ({ children }) => <div>{children}</div>,
  Cell: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
}));

const principalData = {
  departments: [
    { dept: 'AIML', students: 2, faculty: 1, shortage_students: 1, avg_attendance: 75, avg_cgpa: 7.5, by_year: { 3: 2 } },
    { dept: 'CSE', students: 3, faculty: 2, shortage_students: 0, avg_attendance: 85, avg_cgpa: 8, by_year: { 3: 3 } },
  ],
  fee_collection: { total_due: 1000, total_collected: 700, total_outstanding: 300, by_department: {} },
  placements: { upcoming_drives: 2, eligible_finalists: 4, eligible_finalists_by_dept: { AIML: 4 } },
  admissions: { stages: { submitted: 2, verified: 1 }, departments: {} },
};
const adminData = {
  funnel: { stages: { submitted: 1, verified: 0 }, departments: {} },
  applications: [{ id: 7, applicant_name: 'Asha Rao', dept_code: 'AIML', category: 'GM', tenth_pct: 90, twelfth_pct: 88, entrance_score: 160, status: 'submitted', merit_score: null, merit_rank: null, allotted_usn: null, notes: '' }],
};

function renderDashboard(component, role) {
  localStorage.setItem('mawos_token', 'token');
  localStorage.setItem('mawos_user', JSON.stringify({ role, name: role }));
  return render(<BrowserRouter><AuthProvider>{component}</AuthProvider></BrowserRouter>);
}

describe('dashboard API contracts', () => {
  beforeEach(() => { localStorage.clear(); vi.clearAllMocks(); api.campusEvents.mockResolvedValue({ today: [], upcoming: [], events: [] }); });

  it('renders Principal analytics from multiple department rows without the error boundary', async () => {
    api.principalAnalytics.mockResolvedValue(principalData);
    renderDashboard(<PrincipalDashboard />, 'principal');
    await screen.findByText('Institution overview');
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.queryByText('Something went wrong')).not.toBeInTheDocument();
  });

  it('renders an empty Principal department state without crashing', async () => {
    api.principalAnalytics.mockResolvedValue({ ...principalData, departments: [] });
    renderDashboard(<PrincipalDashboard />, 'principal');
    expect(await screen.findByText('No department analytics available')).toBeInTheDocument();
  });

  it('shows controlled states for malformed required and optional Principal data', async () => {
    api.principalAnalytics.mockResolvedValue({ ...principalData, departments: {} });
    renderDashboard(<PrincipalDashboard />, 'principal');
    expect(await screen.findByText('Principal analytics returned an invalid department collection.')).toBeInTheDocument();

    api.principalAnalytics.mockResolvedValue({ ...principalData, fee_collection: null, placements: null, admissions: null });
    renderDashboard(<PrincipalDashboard />, 'principal');
    await waitFor(() => expect(screen.getByText('Financial metrics are unavailable because the API response is incomplete.')).toBeInTheDocument());
    expect(screen.getByText('Admissions metrics are unavailable because the API response is incomplete.')).toBeInTheDocument();
    expect(screen.getByText('Placement metrics unavailable: invalid API response.')).toBeInTheDocument();
  });

  it('renders canonical Admin application fields', async () => {
    api.admissions.mockResolvedValue(adminData);
    renderDashboard(<AdminDashboard />, 'admin');
    expect(await screen.findByText('Asha Rao')).toBeInTheDocument();
    expect(screen.getByText('AIML')).toBeInTheDocument();
    expect(screen.getByText('160')).toBeInTheDocument();
    expect(screen.getAllByText('submitted')).toHaveLength(2);
  });
});
