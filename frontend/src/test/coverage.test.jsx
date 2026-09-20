import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AttendancePanel } from '../pages/faculty/FacultyDashboard';
import { CoverageQueue, FacultyCoverage } from '../pages/coverage/Coverage';
import { api } from '../services/api';

vi.mock('../context/AuthContext', () => ({ useAuth: () => ({ token: 'faculty-token' }) }));
vi.mock('../services/api', () => ({ api: {
  facultyAbsences: vi.fn(), facultyCoverageAssignments: vi.fn(), createFacultyAbsence: vi.fn(),
  facultyAbsenceAction: vi.fn(), facultyCoverageResponse: vi.fn(),
  coverageAbsenceQueue: vi.fn(), coverageRequestQueue: vi.fn(), coverageCandidates: vi.fn(),
  reviewFacultyAbsence: vi.fn(), approveCoverageCandidate: vi.fn(), markCoverageUnfilled: vi.fn(), declineCoverageRequest: vi.fn(),
  attendanceOccurrences: vi.fn(), attendanceOccurrenceRoster: vi.fn(), attendance: vi.fn(),
} }));

const occurrence = { occurrence_id: 31, date: '2030-01-07', department: 'AIML', year: 3,
  section: 'A', subject_code: '23AI51', start_time: '09:00:00', end_time: '10:00:00',
  marking_mode: 'SUBSTITUTE', submitted: false };

describe('faculty absence and runtime coverage UI', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.facultyAbsences.mockResolvedValue([]); api.facultyCoverageAssignments.mockResolvedValue([]);
    api.coverageAbsenceQueue.mockResolvedValue([]); api.coverageRequestQueue.mockResolvedValue([]);
    api.attendanceOccurrences.mockResolvedValue([occurrence]);
    api.attendanceOccurrenceRoster.mockResolvedValue({ occurrence, roster: [
      { usn: '4MT23AI001', name: 'Asha', attendance: 86 },
    ] });
    api.attendance.mockResolvedValue({ accepted: 1, marking_mode: 'SUBSTITUTE' });
  });

  it('keeps the faculty form mobile-safe and shows no private data in empty states', async () => {
    const { container } = render(<FacultyCoverage />);
    expect(await screen.findByText('No absence requests')).toBeInTheDocument();
    expect(screen.getByLabelText('First date')).toHaveAttribute('min');
    expect(screen.getByLabelText('Last date')).toHaveAttribute('min');
    expect(container.querySelector('.grid.min-w-0')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create draft' })).toHaveClass('min-h-11');
  });

  it('loads only backend-eligible candidates and proposes the selected one', async () => {
    api.coverageRequestQueue.mockResolvedValue([{ id: 8, status: 'CANDIDATES_AVAILABLE',
      subject_code: '23AI51', department: 'AIML', year: 3, section: 'A',
      date: '2030-01-07', start_time: '09:00:00', end_time: '10:00:00',
      original_faculty: 'Original Faculty' }]);
    api.coverageCandidates.mockResolvedValue([{ faculty_id: 12, name: 'Qualified Free Faculty',
      daily_load: 2, weekly_load: 8 }]);
    api.approveCoverageCandidate.mockResolvedValue({ assignment_id: 9 });
    render(<CoverageQueue />);
    fireEvent.click(await screen.findByRole('button', { name: 'Show eligible candidates' }));
    expect(await screen.findByText('Qualified Free Faculty')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Propose' }));
    await waitFor(() => expect(api.approveCoverageCandidate).toHaveBeenCalledWith('faculty-token', 8, 12));
  });

  it('uses an immutable scheduled occurrence and submits the server-authorized roster', async () => {
    const onSubmitted = vi.fn();
    const { container } = render(<AttendancePanel token="faculty-token" onSubmitted={onSubmitted} />);
    expect(await screen.findByText(/Attendance sheet · 23AI51/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Date')).not.toBeInTheDocument();
    expect(screen.getByText(/Approved substitute/)).toBeInTheDocument();
    expect(screen.getByLabelText('Attendance roster')).toHaveClass('table-scroll');
    expect(container.querySelector('table')).toHaveClass('min-w-[520px]');
    const submit = screen.getByRole('button', { name: 'Submit attendance' });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);
    await waitFor(() => expect(api.attendance).toHaveBeenCalledWith('faculty-token', {
      occurrence_id: 31, date: '2030-01-07', dept: 'AIML', year: 3, section: 'A',
      subject_code: '23AI51', absent_usns: [],
    }));
    expect(onSubmitted).toHaveBeenCalledWith('Attendance submitted for 23AI51 as substitute faculty.');
  });
});
