import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AttendancePanel } from '../pages/faculty/FacultyDashboard';
import { CoverageQueue, FacultyCoverage } from '../pages/coverage/Coverage';
import { api } from '../services/api';

const mocks = vi.hoisted(() => ({ request: vi.fn() }));

vi.mock('../context/AuthContext', () => ({ useAuth: () => ({ token: 'faculty-token' }) }));
vi.mock('../services/api', () => ({ request: mocks.request, api: {
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
    mocks.request.mockReset();
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

  it('shows a submitted department absence and sends its review decision to the scoped queue API', async () => {
    api.coverageAbsenceQueue.mockResolvedValue([{ id: 41, faculty_name: 'AIML Faculty',
      department: 'AIML', starts_on: '2030-01-07', ends_on: '2030-01-07',
      reason_category: 'PERSONAL', status: 'SUBMITTED' }]);
    api.reviewFacultyAbsence.mockResolvedValue({ id: 41, status: 'APPROVED' });
    render(<CoverageQueue />);
    expect(await screen.findByText('AIML Faculty · AIML')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(api.reviewFacultyAbsence).toHaveBeenCalledWith(
      'faculty-token', 41, 'APPROVE'));
  });

  it('renders accepted coverage as final and hides alternative resolution actions', async () => {
    api.coverageRequestQueue.mockResolvedValue([{ id: 8, status: 'APPROVED', occurrence_id: 31,
      subject_code: '23AI72', department: 'AIML', year: 3, section: 'A', date: '2026-09-23',
      start_time: '10:15:00', end_time: '11:10:00', original_faculty: 'Dr. Vikas Bhat',
      coverage_assignment_status: 'ACCEPTED', substitute_faculty: 'Aditi Kamath',
      accepted_at: '2026-09-22T10:30:00Z', resolution_options: [] }]);
    render(<CoverageQueue />);
    expect(await screen.findByText('Coverage accepted — Aditi Kamath')).toBeInTheDocument();
    expect(screen.getByText(/23 Sept 2026 · 10:15–11:10 · Dr. Vikas Bhat/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Show eligible candidates' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Preview replacement slot' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Preview cancellation' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Decline request' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Mark unresolved' })).not.toBeInTheDocument();
  });

  it('previews and explicitly confirms an absence replacement resolution', async () => {
    api.coverageRequestQueue.mockResolvedValue([{ id: 8, status: 'PENDING', occurrence_id: 31,
      subject_code: '23AI51', department: 'AIML', year: 3, section: 'A', date: '2030-01-07',
      start_time: '09:00:00', end_time: '10:00:00', original_faculty: 'Original Faculty',
      resolution_options: [{ candidate_count: 0 }] }]);
    mocks.request.mockResolvedValueOnce({ preview_id: 'preview-8', confirmation_token: 'token-8',
      correlation_id: 'correlation-8', summary: { message: 'Move this dated class after explicit department confirmation.', replacement_date: '2030-01-08', period_index: 2, room: 'Room 2' } })
      .mockResolvedValueOnce({ mode: 'confirmed' });
    render(<CoverageQueue />);
    fireEvent.click(await screen.findByRole('button', { name: 'Preview replacement slot' }));
    expect(await screen.findByLabelText('Resolution preview for 23AI51')).toHaveTextContent('2030-01-08 · period 3 · Room 2');
    expect(mocks.request).toHaveBeenNthCalledWith(1, '/timetable/operations/preview', { token: 'faculty-token', body: {
      action: 'preview_replacement_slot', department: 'AIML', entry_id: 31, occurrence_date: '2030-01-07',
    } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm reviewed resolution' }));
    await waitFor(() => expect(mocks.request).toHaveBeenNthCalledWith(2, '/timetable/operations/confirm', { token: 'faculty-token', body: {
      preview_id: 'preview-8', confirmation_token: 'token-8',
    } }));
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
