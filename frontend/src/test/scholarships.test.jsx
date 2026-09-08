import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FacultyScholarships, HodScholarships, StudentScholarships } from '../pages/shared/Scholarships';

const mocks = vi.hoisted(() => ({ scholarships: vi.fn(), save: vi.fn(), action: vi.fn() }));
let auth = { token: 'token', user: { role: 'student', dept: 'AIML' } };
vi.mock('../context/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('../services/api', () => ({ api: { scholarships: mocks.scholarships, saveScholarship: mocks.save, scholarshipAction: mocks.action } }));

const item = { id: 8, name: 'Merit Grant', provider: 'Trust', amount: 10000, description: 'Support', application_url: 'https://example.org', closes_at: '2030-01-01T00:00:00Z', status: 'PUBLISHED', eligibility_status: 'ELIGIBLE', criteria: {}, reason_codes: [] };
describe('scholarship workflow pages', () => {
  beforeEach(() => { vi.clearAllMocks(); });
  it('shows student eligibility, empty and loading states', async () => {
    auth = { token: 'token', user: { role: 'student', dept: 'AIML' } }; mocks.scholarships.mockResolvedValue({ scholarships: [item] }); render(<StudentScholarships />);
    expect(screen.getByText(/loading scholarships/i)).toBeInTheDocument(); expect(await screen.findByText('Merit Grant')).toBeInTheDocument(); expect(screen.getByText(/eligible/i)).toBeInTheDocument();
    mocks.scholarships.mockResolvedValue({ scholarships: [] }); render(<StudentScholarships />); expect(await screen.findByText(/no scholarships in this view/i)).toBeInTheDocument();
  });
  it('submits a faculty draft and exposes submit status action', async () => {
    auth = { token: 'token', user: { role: 'faculty', dept: 'AIML' } }; mocks.scholarships.mockResolvedValue({ scholarships: [{ ...item, status: 'DRAFT' }] }); mocks.save.mockResolvedValue({}); mocks.action.mockResolvedValue({}); render(<FacultyScholarships />);
    await screen.findByText('Merit Grant'); fireEvent.click(screen.getByRole('button', { name: 'Submit' })); await waitFor(() => expect(mocks.action).toHaveBeenCalledWith('token', '/faculty/scholarships/8/submit'));
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'New grant' } }); fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'Trust' } }); fireEvent.change(screen.getByLabelText('Opens at'), { target: { value: '2029-01-01T10:00' } }); fireEvent.change(screen.getByLabelText('Closes at'), { target: { value: '2030-01-01T10:00' } }); fireEvent.click(screen.getByRole('button', { name: /save draft/i })); await waitFor(() => expect(mocks.save).toHaveBeenCalled());
  });
  it('supports HOD approval, rejection and change requests', async () => {
    auth = { token: 'token', user: { role: 'hod', dept: 'AIML' } }; mocks.scholarships.mockResolvedValue({ scholarships: [{ ...item, status: 'PENDING_APPROVAL', impact: {} }] }); mocks.action.mockResolvedValue({}); const prompt = vi.spyOn(window, 'prompt'); render(<HodScholarships />); await screen.findByText('Merit Grant'); fireEvent.click(screen.getByRole('button', { name: /approve and publish/i })); await waitFor(() => expect(mocks.action).toHaveBeenCalledWith('token', '/hod/scholarships/8/approve', { comment: '' })); prompt.mockReturnValue('Need a document'); fireEvent.click(screen.getByRole('button', { name: /request changes/i })); await waitFor(() => expect(mocks.action).toHaveBeenCalledWith('token', '/hod/scholarships/8/request-changes', { comment: 'Need a document' })); prompt.mockReturnValue('Not eligible'); fireEvent.click(screen.getByRole('button', { name: 'Reject' })); await waitFor(() => expect(mocks.action).toHaveBeenCalledWith('token', '/hod/scholarships/8/reject', { comment: 'Not eligible' }));
  });
  it('keeps the approval page and shows a safe approval failure', async () => {
    auth = { token: 'token', user: { role: 'hod', dept: 'AIML' } }; mocks.scholarships.mockResolvedValue({ scholarships: [{ ...item, status: 'PENDING_APPROVAL', impact: {} }] }); mocks.action.mockRejectedValue(new Error('Approval could not be completed')); render(<HodScholarships />);
    await screen.findByText('Merit Grant'); fireEvent.click(screen.getByRole('button', { name: /approve and publish/i })); expect(await screen.findByRole('alert')).toHaveTextContent('Approval could not be completed'); expect(screen.getByText('Merit Grant')).toBeInTheDocument();
  });
  it('reports a refresh failure separately after a confirmed approval', async () => {
    auth = { token: 'token', user: { role: 'hod', dept: 'AIML' } }; mocks.scholarships.mockResolvedValueOnce({ scholarships: [{ ...item, status: 'PENDING_APPROVAL', impact: {} }] }).mockRejectedValueOnce(new Error('Refresh failed')); mocks.action.mockResolvedValue({}); render(<HodScholarships />);
    await screen.findByText('Merit Grant'); fireEvent.click(screen.getByRole('button', { name: /approve and publish/i })); expect(await screen.findByText(/approval was saved/i)).toBeInTheDocument(); expect(screen.getByText('Merit Grant')).toBeInTheDocument();
  });
  it('disables approval controls while an approval is pending', async () => {
    let resolve; const pending = new Promise(done => { resolve = done; }); auth = { token: 'token', user: { role: 'hod', dept: 'AIML' } }; mocks.scholarships.mockResolvedValue({ scholarships: [{ ...item, status: 'PENDING_APPROVAL', impact: {} }] }); mocks.action.mockReturnValue(pending); render(<HodScholarships />);
    const approve = await screen.findByRole('button', { name: /approve and publish/i }); fireEvent.click(approve); expect(await screen.findByRole('button', { name: 'Approving…' })).toBeDisabled(); expect(screen.getByRole('button', { name: 'Reject' })).toBeDisabled(); resolve({});
  });
  it('renders API errors safely', async () => { mocks.scholarships.mockRejectedValue(new Error('Forbidden')); render(<StudentScholarships />); expect(await screen.findByRole('alert')).toHaveTextContent('Forbidden'); });
});
