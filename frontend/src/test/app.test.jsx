import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { BrowserRouter, MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { vi } from 'vitest';
import { AuthProvider } from '../context/AuthContext';
import LoginPage from '../pages/auth/LoginPage';
import { ConfirmDialog, ErrorState } from '../components/ui';
import { api } from '../services/api';

vi.mock('../services/api', () => ({ api: { login: vi.fn() }, setUnauthorizedHandler: vi.fn(), ApiError: class ApiError extends Error {} }));
const renderLogin = () => render(<BrowserRouter><AuthProvider><LoginPage /></AuthProvider></BrowserRouter>);
function LocationProbe() { return <output data-testid="location">{useLocation().pathname}</output>; }

describe('login', () => {
  beforeEach(() => { localStorage.clear(); vi.clearAllMocks(); });
  it('validates required credentials', () => { renderLogin(); fireEvent.click(screen.getByRole('button', { name: /sign in to student/i })); expect(screen.getByRole('alert')).toHaveTextContent('Enter your ID and password'); });
  it('uses the authenticated role for redirect state', async () => { api.login.mockResolvedValue({ token: 'token', user: { role: 'faculty', name: 'Faculty' }, ai_mode: 'lexicon' }); renderLogin(); fireEvent.change(screen.getByLabelText(/user id/i), { target: { value: 'aiml.f02' } }); fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: 'faculty123' } }); fireEvent.click(screen.getByRole('button', { name: /sign in to student/i })); await waitFor(() => expect(api.login).toHaveBeenCalledWith('aiml.f02', 'faculty123')); expect(JSON.parse(localStorage.getItem('mawos_user'))).toMatchObject({ role: 'faculty' }); });
  it('rejects a saved faculty scholarship return URL when an HOD logs in', async () => {
    api.login.mockResolvedValue({ token: 'hod-token', user: { role: 'hod', name: 'HOD' } });
    render(<MemoryRouter initialEntries={[{ pathname: '/login', state: { from: { pathname: '/faculty/scholarships' } } }]}><AuthProvider><Routes><Route path="/login" element={<LoginPage />} /><Route path="/hod" element={<LocationProbe />} /><Route path="/faculty/scholarships" element={<LocationProbe />} /></Routes></AuthProvider></MemoryRouter>);
    fireEvent.change(screen.getByLabelText(/user id/i), { target: { value: 'aiml.h01' } }); fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: 'hod123' } }); fireEvent.click(screen.getByRole('button', { name: /sign in to student/i }));
    expect(await screen.findByTestId('location')).toHaveTextContent('/hod');
  });
});

describe('shared interaction states', () => {
  it('requires confirmation before calling an admin action', () => { const confirm = vi.fn(); render(<ConfirmDialog open title="Allot seats?" onConfirm={confirm} onClose={() => {}}>This changes data.</ConfirmDialog>); expect(confirm).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole('button', { name: 'Confirm' })); expect(confirm).toHaveBeenCalledTimes(1); });
  it('shows backend error state', () => { render(<ErrorState error={new Error('Backend offline')} />); expect(screen.getByText('Backend offline')).toBeInTheDocument(); });
});
