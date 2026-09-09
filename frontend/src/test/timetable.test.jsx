import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AdminTimetable, HodTimetable, PersonalTimetable, PrincipalTimetable } from '../pages/timetable/Timetable';
import { RoleRoute } from '../components/routes';
import { isRouteAllowedForRole } from '../routes/roleRoutes';
import { navigationByRole } from '../layouts/AppLayout';

const mocks = vi.hoisted(() => ({ request: vi.fn() }));
let auth = { token: 'test-token', user: { role: 'hod', username: 'test' } };
vi.mock('../context/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('../services/api', () => ({ request: mocks.request, api: {}, ApiError: class extends Error {} }));
const terms = [{ id: 1, name: 'Odd term', starts_on: '2026-09-01', ends_on: '2026-12-31' }];
const config = { sections: [], requirements: [], rooms: [], assignments: [], faculty: [], subjects: [], qualifications: [], limits: [], periods: [] };
const empty = { published: false, current: null, next: null, today: [], weekly: [], message: 'No published timetable for the current academic term.', next_message: 'No remaining published classes.' };
const run = {
  id: 11, status: 'COMPLETE', metrics: { placed: 1, required: 1, steps: 20, search_conflicts: 2, duration_ms: 30 }, conflicts: [], unplaced: [],
  entries: [{ id: 101, requirement_id: 1, occurrence: 0, section_id: 1, subject: 'CS51', faculty_id: 1, room_id: 1, day: 0, period_index: 0, locked: false }],
  configuration: { data: { periods: [{ day: 0, index: 0, start: 540, end: 600 }] }, metadata: { sections: { 1: { year: 3, semester: 5, name: 'A' } }, faculty: { 1: 'Professor Rao' }, rooms: { 1: 'Room 101' }, subjects: { CS51: 'Algorithms' } } },
};
let generated, published, initialHistory;
function responder(path, options = {}) {
  if (path === '/timetable/terms') return Promise.resolve(terms);
  if (path.includes('/configuration') && path.startsWith('/hod')) return Promise.resolve(config);
  if (path.startsWith('/timetable/runs?')) return Promise.resolve(initialHistory);
  if (path === '/timetable/runs/11') return Promise.resolve(run);
  if (path.endsWith('/preflight')) return Promise.resolve({ ready: true, required_periods: 1, issues: [] });
  if (path.endsWith('/bootstrap')) return Promise.resolve({ mode: options.body.apply ? 'apply' : 'dry-run',
    preview_hash: 'a'.repeat(64),
    departments: [{ code: 'AIML' }], sections: [{ state: 'would_create' }],
    faculty_assignments: [{ id: 1 }], requirements: [{ state: 'would_create' }],
    missing_weekly_period_values: [], missing_rooms: [], defaulted_values: [], conflicts: [] });
  if (path === '/hod/timetable/terms/1/runs') return generated();
  if (path.endsWith('/publish')) return published();
  if (path.endsWith('/lock')) return Promise.resolve({ ...run, entries: [{ ...run.entries[0], locked: options.body.locked }] });
  if (path === '/faculty/timetable' || path === '/student/timetable') return Promise.resolve(empty);
  if (path === '/admin/timetable/configuration') return Promise.resolve({ departments: [], rooms: [] });
  if (path === '/principal/timetable/overview') return Promise.resolve([]);
  return Promise.reject(new Error(`Unexpected ${path}`));
}
async function workspace() {
  render(<HodTimetable />);
  fireEvent.change(await screen.findByLabelText('Academic term'), { target: { value: '1' } });
  await screen.findByRole('button', { name: 'Generate Draft' });
  await screen.findByText('No drafts or published versions yet.');
}

beforeEach(() => {
  vi.clearAllMocks();
  auth = { token: 'test-token', user: { role: 'hod', username: 'test' } };
  initialHistory = [];
  generated = () => Promise.resolve(run);
  published = () => Promise.resolve({ ...run, status: 'PUBLISHED' });
  mocks.request.mockImplementation(responder);
});

describe('published personal timetables', () => {
  it.each(['student', 'faculty'])('shows a clear unpublished empty state for %s', async role => {
    render(<PersonalTimetable role={role} />);
    expect(await screen.findByText('Current class')).toBeInTheDocument();
    expect(screen.getAllByText(empty.message).length).toBeGreaterThan(0);
    expect(screen.getByText('No remaining published classes.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Publish/ })).not.toBeInTheDocument();
    expect(mocks.request).toHaveBeenCalledWith(`/${role}/timetable`, expect.objectContaining({ token: 'test-token' }));
  });

  it('renders current/next names, faculty, room and weekly schedule from server results', async () => {
    const e = { id: 1, day: 0, period_index: 0, subject_code: 'CS51', subject_name: 'Algorithms', faculty: 'Professor Rao', room: 'Room 101', section: '3A', date: '2026-09-07', start_time: '09:00', end_time: '10:00' };
    mocks.request.mockResolvedValue({ ...empty, published: true, current: e, next: { ...e, id: 2, date: '2026-09-08' }, today: [e], weekly: [e] });
    render(<PersonalTimetable role="student" />);
    expect((await screen.findAllByText('Algorithms')).length).toBeGreaterThan(1);
    expect(screen.getByText('2026-09-08 · 09:00–10:00')).toBeInTheDocument();
    const weeklyCell = screen.getByLabelText(/Monday 09:00–09:55: CS51 Algorithms/);
    expect(within(weeklyCell).getByText('Professor Rao')).toBeInTheDocument();
    expect(within(weeklyCell).getByText('Room 101')).toBeInTheDocument();
  });

  it('renders the complete responsive weekly grid and places classes by day and period', async () => {
    const monday = { id: 1, day: 0, period_index: 0, subject_code: '23AI72', subject_name: 'Operating Systems', faculty: 'Dr. Vikas Bhat', room: 'AIML Classroom 2', section: 'AIML 4A / semester 7', date: '2026-09-07', start_time: '09:00', end_time: '09:55' };
    mocks.request.mockResolvedValue({ ...empty, published: true, current: monday, next: null, today: [monday], weekly: [monday] });
    render(<PersonalTimetable role="student" />);

    expect(await screen.findByRole('columnheader', { name: 'Monday' })).toBeInTheDocument();
    for (const day of ['Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']) {
      expect(screen.getByRole('columnheader', { name: day })).toBeInTheDocument();
    }
    for (const time of ['09:00–09:55', '10:15–11:10', '11:10–12:05', '12:50–13:50', '13:50–14:50', '15:15–16:30']) {
      expect(screen.getByRole('rowheader', { name: time })).toBeInTheDocument();
    }
    const occupied = screen.getByLabelText(/Monday 09:00–09:55: 23AI72 Operating Systems/);
    expect(within(occupied).getByText('23AI72 · Operating Systems')).toBeInTheDocument();
    expect(within(occupied).getByText('Dr. Vikas Bhat')).toBeInTheDocument();
    expect(within(occupied).getByText('AIML Classroom 2')).toBeInTheDocument();
    expect(within(occupied).getByText('Section A · Semester 7')).toBeInTheDocument();
    expect(screen.getByLabelText('Tuesday 09:00–09:55: No class')).toHaveTextContent('—');
    expect(screen.getByText('Lunch break')).toBeInTheDocument();
    expect(screen.getByTestId('timetable-scroll')).toHaveClass('overflow-x-auto');
    expect(screen.getByRole('rowheader', { name: '09:00–09:55' })).toHaveClass('sticky');
  });

  it.each(['student', 'faculty'])('keeps the %s timetable scoped to its role-specific endpoint', async role => {
    const own = { id: 5, day: 2, period_index: 3, subject_code: 'OWN1', subject_name: 'Authorized class', faculty: 'Assigned faculty', room: 'Room 4', section: 'AIML 4A / semester 7', start_time: '12:50', end_time: '13:50' };
    mocks.request.mockImplementation((path, options) => path === `/${role}/timetable`
      ? Promise.resolve({ ...empty, published: true, today: [], weekly: [own] })
      : responder(path, options));
    render(<PersonalTimetable role={role} />);
    expect(await screen.findByLabelText(/Wednesday 12:50–13:50: OWN1 Authorized class/)).toBeInTheDocument();
    expect(mocks.request).toHaveBeenCalledWith(`/${role}/timetable`, expect.objectContaining({ token: 'test-token' }));
  });

  it('renders controlled network errors and no stale timetable', async () => {
    mocks.request.mockRejectedValue(new Error('Network request failed'));
    render(<PersonalTimetable role="student" />);
    expect(await screen.findByText('Network request failed')).toBeInTheDocument();
    expect(screen.queryByText('Algorithms')).not.toBeInTheDocument();
  });

  it('preserves unsaved faculty availability selections during schedule refresh', async () => {
    mocks.request.mockImplementation((path, options) => path === '/faculty/timetable/terms/1/availability'
      ? Promise.resolve({ periods: [{ id: 9, day_of_week: 0, starts_at: '09:00', ends_at: '10:00', is_break: false, is_closed: false }], unavailable_period_ids: [] })
      : responder(path, options));
    let refresh;
    render(<PersonalTimetable role="faculty" onRefreshScheduled={fn => { refresh = fn; }} />);
    const termPicker = await screen.findByLabelText('Academic term');
    await screen.findByRole('option', { name: /Odd term/ });
    fireEvent.change(termPicker, { target: { value: '1' } });
    const checkbox = await screen.findByRole('checkbox', {}, { timeout: 5000 });
    fireEvent.click(checkbox);
    expect(checkbox).toBeChecked();
    // Trigger the already-installed refresh callback without waiting 30 seconds.
    // The server response must update the schedule without remounting the editor.
    expect(refresh).toBeTypeOf('function');
    await act(async () => { await refresh(); });
    expect(screen.getByRole('checkbox')).toBeChecked();
    expect(mocks.request.mock.calls.filter(([p]) => p === '/faculty/timetable')).toHaveLength(2);
  }, 10000);

  it('shows loading until the server answers', async () => {
    let resolve;
    mocks.request.mockReturnValue(new Promise(r => { resolve = r; }));
    render(<PersonalTimetable role="student" />);
    expect(screen.queryByText('Current class')).not.toBeInTheDocument();
    resolve(empty);
    expect(await screen.findByText('Current class')).toBeInTheDocument();
  });
});

describe('HOD draft workflow', () => {
  it('sends the controlled subject-credit selection in the dry-run request', async () => {
    await workspace();
    fireEvent.change(screen.getByLabelText('Bootstrap weekly periods'), { target: { value: '30' } });
    fireEvent.click(screen.getByText('Use subject credits explicitly'));
    fireEvent.change(screen.getByLabelText('Bootstrap faculty daily limit'), { target: { value: '5' } });
    fireEvent.change(screen.getByLabelText('Bootstrap faculty weekly limit'), { target: { value: '24' } });

    expect(screen.getByLabelText('Use subject credits explicitly')).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Preview dry run' }));

    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith('/timetable/terms/1/bootstrap', {
      token: 'test-token',
      body: {
        term_id: 1,
        fixed_weekly_periods: 30,
        use_subject_credits: true,
        faculty_daily_limit: 5,
        faculty_weekly_limit: 24,
      },
    }));
  });

  it('requires a dry-run preview and explicit confirmation before bootstrap apply', async () => {
    await workspace();
    const apply = screen.getByRole('button', { name: 'Apply previewed changes' });
    expect(apply).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Preview dry run' }));
    expect(await screen.findByText('Dry-run preview loaded. No data was written.')).toBeInTheDocument();
    expect(apply).toBeDisabled();
    fireEvent.click(screen.getByLabelText('I reviewed this preview and confirm the import'));
    expect(apply).toBeEnabled();
    fireEvent.click(apply);
    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith('/timetable/terms/1/bootstrap',
      expect.objectContaining({ body: expect.objectContaining({ apply: true, confirm_apply: true }) })));
  });

  it('filters mappings by section and explains assignments in another section', async () => {
    const mapped = { ...config,
      sections: [{ id: 1, year: 1, semester: 1, name: 'A' }, { id: 2, year: 1, semester: 1, name: 'B' }],
      subjects: [{ code: '23AI12', name: 'Deep Learning', semester: 1 }],
      faculty: [{ id: 8, name: 'Aditi Kamath' }],
      assignments: [{ id: 7, year: 1, section: 'B', subject_code: '23AI12', faculty_id: 8 }] };
    mocks.request.mockImplementation((p, o) => p.includes('/configuration') && p.startsWith('/hod') ? Promise.resolve(mapped) : responder(p, o));
    await workspace();
    const subject = screen.getAllByLabelText('Mapped subject')[0];
    fireEvent.change(subject, { target: { value: '23AI12' } });
    expect(await screen.findByText('Deep Learning (23AI12) is assigned to Year 1 Section B in the database, not Year 1 Section A.')).toBeInTheDocument();
    expect(screen.getAllByLabelText('Mapped faculty')[0]).toBeDisabled();
  });

  it('checks readiness, generates a draft and never automatically publishes', async () => {
    await workspace();
    fireEvent.click(screen.getByRole('button', { name: 'Generate Draft' }));
    expect(await screen.findByText('Version 11 · COMPLETE')).toBeInTheDocument();
    expect(screen.getByText('No hard violations.')).toBeInTheDocument();
    expect(screen.getByText('CS51 · Algorithms')).toBeInTheDocument();
    expect(screen.getByLabelText('Preview section')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Monday' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Lock CS51 Mon 09:00' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Validate draft' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Publish complete timetable' })).toBeInTheDocument();
    expect(mocks.request.mock.calls.some(([p]) => p.endsWith('/publish'))).toBe(false);
  });

  it('prevents duplicate generate and publish clicks while requests are pending', async () => {
    let finishGeneration, finishPublication;
    generated = () => new Promise(resolve => { finishGeneration = resolve; });
    published = () => new Promise(resolve => { finishPublication = resolve; });
    await workspace();
    const generate = screen.getByRole('button', { name: 'Generate Draft' });
    fireEvent.click(generate); fireEvent.click(generate);
    await waitFor(() => expect(finishGeneration).toBeTypeOf('function'));
    expect(mocks.request.mock.calls.filter(([p]) => p === '/hod/timetable/terms/1/runs')).toHaveLength(1);
    expect(generate).toBeDisabled();
    finishGeneration(run);
    const publish = await screen.findByRole('button', { name: 'Publish complete timetable' });
    await waitFor(() => expect(publish).toBeEnabled());
    fireEvent.click(publish); fireEvent.click(publish);
    await waitFor(() => expect(finishPublication).toBeTypeOf('function'));
    expect(mocks.request.mock.calls.filter(([p]) => p.endsWith('/publish'))).toHaveLength(1);
    expect(publish).toBeDisabled();
    finishPublication({ ...run, status: 'PUBLISHED' });
    expect(await screen.findByText('Version 11 · PUBLISHED')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Publish complete timetable' })).toBeDisabled();
  });

  it('shows hard conflicts and unplaced requirements and disables partial publication', async () => {
    generated = () => Promise.resolve({ ...run, status: 'PARTIAL', conflicts: [{ code: 'weekly_periods', message: 'Requires four periods.' }], unplaced: [{ requirement_id: 1, subject: 'CS51', missing_periods: 3, reason: 'Search budget exhausted.' }] });
    await workspace();
    fireEvent.click(screen.getByRole('button', { name: 'Generate Draft' }));
    expect(await screen.findByText('Requires four periods.')).toBeInTheDocument();
    expect(screen.getByText(/CS51: 3 missing periods/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Publish complete timetable' })).toBeDisabled();
  });

  it('does not generate when preflight fails and displays actionable issues', async () => {
    mocks.request.mockImplementation((p, o) => p.endsWith('/preflight') ? Promise.resolve({ ready: false, issues: [{ message: 'Add a lab room with capacity 60.' }] }) : responder(p, o));
    await workspace();
    fireEvent.click(screen.getByRole('button', { name: 'Generate Draft' }));
    expect(await screen.findByText('Add a lab room with capacity 60.')).toBeInTheDocument();
    expect(mocks.request.mock.calls.some(([p]) => p === '/hod/timetable/terms/1/runs')).toBe(false);
  });

  it('locks an entry through the server and regenerates from the source draft', async () => {
    await workspace();
    fireEvent.click(screen.getByRole('button', { name: 'Generate Draft' }));
    const lock = await screen.findByRole('button', { name: 'Lock CS51 Mon 09:00' });
    await waitFor(() => expect(lock).toBeEnabled());
    fireEvent.click(lock);
    expect(await screen.findByRole('button', { name: 'Unlock CS51 Mon 09:00' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Generate Draft' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Generate Draft' }));
    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith('/hod/timetable/terms/1/runs', expect.objectContaining({ body: { seed: 7, parent_run_id: 11 } })));
  });

  it('shows a controlled publish failure without labeling the draft as published', async () => {
    published = () => Promise.reject(new Error('Publication validation failed.'));
    await workspace();
    fireEvent.click(screen.getByRole('button', { name: 'Generate Draft' }));
    const publish = await screen.findByRole('button', { name: 'Publish complete timetable' });
    await waitFor(() => expect(publish).toBeEnabled());
    fireEvent.click(publish);
    expect(await screen.findByText('Publication validation failed.')).toBeInTheDocument();
    expect(screen.getByText('Version 11 · COMPLETE')).toBeInTheDocument();
  });
});

describe('configuration, overview and role routes', () => {
  it('exposes admin term and room configuration', async () => {
    render(<AdminTimetable />);
    expect(await screen.findByText('Timetable configuration')).toBeInTheDocument();
    expect(screen.getByText('No rooms configured.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save academic term' })).toBeInTheDocument();
  });

  it('keeps principal coverage read-only and distinguishes drafts from publication', async () => {
    mocks.request.mockResolvedValue([{ term_id: 1, department: 'CSE', term: 'Odd term', published_run_id: null, latest_status: 'PARTIAL', published_metrics: null }]);
    render(<PrincipalTimetable />);
    expect(await screen.findByText('No published timetable')).toBeInTheDocument();
    expect(screen.getByText('Latest run: PARTIAL')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('redirects a student before mounting the HOD route', async () => {
    auth = { ...auth, user: { role: 'student' } };
    render(<MemoryRouter initialEntries={['/hod/timetable']}><Routes><Route element={<RoleRoute roles={['hod']} />}><Route path="/hod/timetable" element={<HodTimetable />} /></Route><Route path="/student" element={<p>Student home</p>} /></Routes></MemoryRouter>);
    expect(await screen.findByText('Student home')).toBeInTheDocument();
    expect(mocks.request).not.toHaveBeenCalled();
  });

  it.each([['hod', '/hod/timetable'], ['admin', '/admin/timetable'], ['principal', '/principal/timetable']])('registers the %s timetable route and navigation', (role, path) => {
    expect(isRouteAllowedForRole(role, path)).toBe(true);
    expect(isRouteAllowedForRole('student', path)).toBe(false);
    expect(navigationByRole[role]).toContainEqual(expect.objectContaining({ to: path }));
  });
});
