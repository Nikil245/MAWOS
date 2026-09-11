import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AdminPlacements, StudentPlacements } from '../pages/placement/Placements';
import { RoleRoute } from '../components/routes';
import { navigationByRole } from '../layouts/AppLayout';
import { isRouteAllowedForRole } from '../routes/roleRoutes';

const mocks = vi.hoisted(() => Object.fromEntries(['placementDrives', 'savePlacementDrive', 'placementAction', 'placementShortlist', 'placementEligibility', 'placementOutcomes', 'savePlacementOutcome'].map(key => [key, vi.fn()])));
let auth;
vi.mock('../context/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('../services/api', () => ({ api: mocks, ApiError: Error }));
const drive = { id: 1, company: 'Example', role: 'Engineer', package_lpa: 8, drive_date: '2026-10-01', departments: 'AIML', min_cgpa: 6, max_backlogs: 0, min_attendance: 75, status: 'OPEN', requires_fee_clearance: false, application_deadline: null, candidate_count: 0, shortlisted_count: 0 };
const entry = { usn: 'P4', name: 'My name', drive, eligible: true, status: 'EVALUATED', ml_probability: null, model_version: null, reasons: 'Meets all drive criteria (model unavailable, rules-only evaluation)' };

beforeEach(() => {
  vi.clearAllMocks();
  auth = { token: 'admin-token', user: { role: 'admin' } };
  mocks.placementDrives.mockResolvedValue([drive]);
  mocks.placementShortlist.mockResolvedValue([entry]);
  mocks.placementOutcomes.mockResolvedValue([]);
  mocks.placementEligibility.mockResolvedValue(entry);
  mocks.placementAction.mockResolvedValue({});
  mocks.savePlacementDrive.mockResolvedValue({});
  mocks.savePlacementOutcome.mockResolvedValue({});
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});

describe('placement administration', () => {
  it('lists drives, validates and submits normalized create fields', async () => {
    render(<AdminPlacements />);
    expect(await screen.findByRole('cell', { name: 'Example Engineer' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Company'), { target: { value: 'New Co' } });
    fireEvent.change(screen.getByLabelText('Role'), { target: { value: 'Developer' } });
    fireEvent.change(screen.getByLabelText('Package (LPA)'), { target: { value: '10' } });
    fireEvent.change(screen.getByLabelText('Drive date'), { target: { value: '2026-10-02' } });
    fireEvent.change(screen.getByLabelText('Departments'), { target: { value: ' aiml ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create drive' }));
    await waitFor(() => expect(mocks.savePlacementDrive).toHaveBeenCalledWith('admin-token', null, expect.objectContaining({ company: 'New Co', departments: 'AIML', package_lpa: 10 })));
    expect(await screen.findByText('Drive saved.')).toBeInTheDocument();
  });

  it('edits an open drive and shows backend validation errors', async () => {
    mocks.savePlacementDrive.mockRejectedValue(new Error('Unknown department code in eligible list'));
    render(<AdminPlacements />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    expect(screen.getByLabelText('Company')).toHaveValue('Example');
    fireEvent.click(screen.getByRole('button', { name: 'Save drive' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Unknown department');
    expect(mocks.savePlacementDrive).toHaveBeenCalledWith('admin-token', 1, expect.any(Object));
  });

  it('generates shortlist and displays reasons/model information', async () => {
    mocks.placementShortlist.mockResolvedValue([{ ...entry, ml_probability: 0.81, model_version: 'rf-test', reasons: 'First reason; Second reason' }]);
    render(<AdminPlacements />);
    fireEvent.click(await screen.findByRole('button', { name: 'Generate shortlist' }));
    expect(await screen.findByText('Shortlist generated.')).toBeInTheDocument();
    expect(mocks.placementAction).toHaveBeenCalledWith('admin-token', 1, 'shortlist', { regenerate: false });
    expect(screen.getByText('Model evaluated · 0.81')).toBeInTheDocument();
    expect(screen.getByText('Model version: rf-test')).toBeInTheDocument();
    expect(screen.getByText('First reason')).toBeInTheDocument();
    expect(screen.getByText('Second reason')).toBeInTheDocument();
  });

  it('requires explicit confirmation for regeneration', async () => {
    mocks.placementDrives.mockResolvedValue([{ ...drive, status: 'SHORTLIST_GENERATED', candidate_count: 1 }]);
    window.confirm.mockReturnValueOnce(false).mockReturnValueOnce(true);
    render(<AdminPlacements />);
    const button = await screen.findByRole('button', { name: 'Regenerate shortlist' });
    fireEvent.click(button);
    expect(mocks.placementAction).not.toHaveBeenCalled();
    fireEvent.click(button);
    await waitFor(() => expect(mocks.placementAction).toHaveBeenCalledWith('admin-token', 1, 'shortlist', { regenerate: true }));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('including finalized candidates'));
  });

  it('confirms close and cancel operations', async () => {
    render(<AdminPlacements />);
    fireEvent.click(await screen.findByRole('button', { name: 'Close', exact: true }));
    await waitFor(() => expect(mocks.placementAction).toHaveBeenCalledWith('admin-token', 1, 'close'));
    await screen.findByText('Drive status updated.');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel drive' }));
    await waitFor(() => expect(mocks.placementAction).toHaveBeenCalledWith('admin-token', 1, 'cancel'));
  });

  it('records outcomes and surfaces single-offer conflicts with an explicit override', async () => {
    mocks.savePlacementOutcome.mockRejectedValueOnce(new Error('Student already has an accepted offer'));
    render(<AdminPlacements />);
    fireEvent.click(await screen.findByRole('button', { name: 'View shortlist / outcomes' }));
    fireEvent.change(await screen.findByLabelText('Student USN'), { target: { value: 'p4' } });
    fireEvent.change(screen.getByLabelText('Outcome status'), { target: { value: 'OFFER_ACCEPTED' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save outcome' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('already has an accepted offer');
    fireEvent.click(screen.getByLabelText('Allow multiple accepted offers'));
    fireEvent.click(screen.getByRole('button', { name: 'Save outcome' }));
    expect(await screen.findByText('Outcome saved.')).toBeInTheDocument();
    expect(mocks.savePlacementOutcome).toHaveBeenLastCalledWith('admin-token', 1, 'P4', { outcome_status: 'OFFER_ACCEPTED', package_offered: null, allow_multiple_offers: true });
  });
});

describe('student placement scope and role routing', () => {
  it('fetches only the authenticated USN and shows rules-only results without controls', async () => {
    auth = { token: 'student-token', user: { role: 'student', usn: 'P4' } };
    render(<StudentPlacements />);
    expect(screen.getByText('Loading placements…')).toBeInTheDocument();
    expect(await screen.findByText('Rules-only evaluation')).toBeInTheDocument();
    expect(mocks.placementEligibility).toHaveBeenCalledWith('student-token', 1, 'P4');
    expect(mocks.placementShortlist).not.toHaveBeenCalled();
    expect(mocks.placementOutcomes).not.toHaveBeenCalled();
    expect(screen.queryByText('Record outcome')).not.toBeInTheDocument();
    expect(screen.queryByText('Generate shortlist')).not.toBeInTheDocument();
  });

  it('shows model evaluation without a numeric score or rank', async () => {
    auth = { token: 'student-token', user: { role: 'student', usn: 'P4' } };
    mocks.placementEligibility.mockResolvedValue({ ...entry, ml_probability: 0.85, model_version: 'rf-test', reasons: 'Model confidence 0.85 meets threshold 0.5' });
    render(<StudentPlacements />);
    expect(await screen.findByText('Model evaluated')).toBeInTheDocument();
    expect(screen.queryByText('Model evaluated · 0.85')).not.toBeInTheDocument();
    expect(screen.queryByText('Model version: rf-test')).not.toBeInTheDocument();
  });

  it('clears old student results on account changes', async () => {
    auth = { token: 'first', user: { role: 'student', usn: 'P4' } };
    const view = render(<StudentPlacements />);
    await screen.findByText('Rules-only evaluation');
    auth = { token: 'second', user: { role: 'student', usn: 'P3' } };
    mocks.placementEligibility.mockResolvedValue({ ...entry, usn: 'P3', status: 'NOT_EVALUATED', eligible: false, reasons: 'Placements open in final year' });
    view.rerender(<StudentPlacements />);
    expect(screen.queryByText(entry.reasons)).not.toBeInTheDocument();
    expect(await screen.findByText('Placements open in final year')).toBeInTheDocument();
    expect(mocks.placementEligibility).toHaveBeenLastCalledWith('second', 1, 'P3');
  });

  it.each(['student', 'faculty', 'hod', 'principal'])('blocks %s from admin UI and API calls', async role => {
    auth = { token: 'other', user: { role, usn: 'P4' } };
    render(<MemoryRouter initialEntries={['/admin/placements']}><Routes>
      <Route element={<RoleRoute roles={['admin']} />}><Route path="/admin/placements" element={<AdminPlacements />} /></Route>
      <Route path={`/${role}`} element={<p>Own dashboard</p>} />
    </Routes></MemoryRouter>);
    expect(await screen.findByText('Own dashboard')).toBeInTheDocument();
    expect(mocks.placementDrives).not.toHaveBeenCalled();
    expect(isRouteAllowedForRole(role, '/admin/placements')).toBe(false);
  });

  it('uses role-safe navigation', () => {
    expect(navigationByRole.admin).toContainEqual(expect.objectContaining({ to: '/admin/placements' }));
    expect(navigationByRole.student).toContainEqual(expect.objectContaining({ to: '/student/placements' }));
    expect(isRouteAllowedForRole('admin', '/student/placements')).toBe(false);
    for (const role of ['faculty', 'hod', 'principal']) expect(navigationByRole[role].some(item => item.to.includes('/placements'))).toBe(false);
  });

  it('handles an empty list', async () => {
    auth = { token: 'student-token', user: { role: 'student', usn: 'P4' } };
    mocks.placementDrives.mockResolvedValue([]);
    render(<StudentPlacements />);
    expect(await screen.findByText('No placement drives available.')).toBeInTheDocument();
  });

  it('handles failed eligibility reads and retries', async () => {
    auth = { token: 'student-token', user: { role: 'student', usn: 'P4' } };
    mocks.placementEligibility.mockRejectedValueOnce(new Error('Service unavailable'));
    render(<StudentPlacements />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Service unavailable');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Rules-only evaluation')).toBeInTheDocument();
  });
});
