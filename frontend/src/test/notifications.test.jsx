import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AppLayout } from '../layouts/AppLayout';

const mocks = vi.hoisted(() => ({ notifications: vi.fn() }));
let auth = { token: 'token-a', user: { username: 'student-a', name: 'Student A', role: 'student' }, logout: vi.fn() };
const { notifications } = mocks;

vi.mock('../context/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('../services/api', () => ({
  api: { notifications: mocks.notifications },
  ApiError: class ApiError extends Error {},
}));

const unread = { id: 1, title: 'Attendance alert', message: 'Your attendance needs attention.', source_agent: 'notification_agent', at: '2026-01-02T03:04:05Z', read: false };
const read = { id: 2, title: 'Timetable updated', message: 'A new timetable is available.', source_agent: 'timetable_agent', at: '2026-01-01T03:04:05Z', read: true };

function renderLayout() {
  return render(<MemoryRouter><Routes><Route element={<AppLayout />}><Route index element={<div>Dashboard</div>} /></Route></Routes></MemoryRouter>);
}

async function openDrawer() {
  fireEvent.click(screen.getByRole('button', { name: /open notifications/i }));
  return screen.findByRole('heading', { name: 'Notifications' });
}

describe('shared notifications', () => {
  beforeEach(() => {
    auth = { token: 'token-a', user: { username: 'student-a', name: 'Student A', role: 'student' }, logout: vi.fn() };
    notifications.mockReset();
  });

  it('shows fetched notifications and the correct unread bell count', async () => {
    notifications.mockResolvedValue({ notifications: [unread, read], unread_count: 1 });
    renderLayout();
    await waitFor(() => expect(notifications).toHaveBeenCalled());
    await openDrawer();
    expect(await screen.findByText('Attendance alert')).toBeInTheDocument();
    expect(screen.getByText('Your attendance needs attention.')).toBeInTheDocument();
    expect(screen.getByText(/notification_agent/)).toBeInTheDocument();
    expect(screen.getByText('Unread')).toBeInTheDocument();
  });

  it('uses the fetched unread state for the bell badge', async () => {
    notifications.mockResolvedValue({ notifications: [unread, read], unread_count: 1 });
    renderLayout();
    await waitFor(() => expect(screen.getByRole('button', { name: /1 unread/i })).toBeInTheDocument());
  });

  it('shows an empty state only after a successful empty response', async () => {
    notifications.mockResolvedValue({ notifications: [], unread_count: 0 });
    renderLayout();
    await openDrawer();
    expect(await screen.findByText('No notifications')).toBeInTheDocument();
  });

  it('shows an API error with retry instead of the empty state', async () => {
    notifications.mockRejectedValue(new Error('Backend offline'));
    renderLayout();
    await openDrawer();
    expect(await screen.findByText('Backend offline')).toBeInTheDocument();
    expect(screen.queryByText('No notifications')).not.toBeInTheDocument();
    notifications.mockResolvedValue({ notifications: [], unread_count: 0 });
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(screen.getByText('No notifications')).toBeInTheDocument());
  });

  it('refreshes notifications when the drawer opens', async () => {
    notifications.mockResolvedValue({ notifications: [unread], unread_count: 1 });
    renderLayout();
    await waitFor(() => expect(screen.getByRole('button', { name: /1 unread/i })).toBeInTheDocument());
    await openDrawer();
    await waitFor(() => expect(notifications).toHaveBeenCalledTimes(2));
  });

  it('clears old notifications when the logged-in account changes', async () => {
    notifications.mockResolvedValue({ notifications: [unread], unread_count: 1 });
    const view = renderLayout();
    await openDrawer();
    expect(await screen.findByText('Attendance alert')).toBeInTheDocument();
    notifications.mockResolvedValue({ notifications: [], unread_count: 0 });
    auth = { token: 'token-b', user: { username: 'student-b', name: 'Student B', role: 'student' }, logout: vi.fn() };
    view.rerender(<MemoryRouter><Routes><Route element={<AppLayout />}><Route index element={<div>Dashboard</div>} /></Route></Routes></MemoryRouter>);
    await waitFor(() => expect(screen.queryByText('Attendance alert')).not.toBeInTheDocument());
  });

  it('clears notifications when logout removes the session', async () => {
    notifications.mockResolvedValue({ notifications: [unread], unread_count: 1 });
    const view = renderLayout();
    await openDrawer();
    expect(await screen.findByText('Attendance alert')).toBeInTheDocument();
    auth = { token: null, user: null, logout: vi.fn() };
    view.rerender(<MemoryRouter><Routes><Route element={<AppLayout />}><Route index element={<div>Dashboard</div>} /></Route></Routes></MemoryRouter>);
    await waitFor(() => expect(screen.queryByText('Attendance alert')).not.toBeInTheDocument());
  });

  it('does not display a delayed response from the previous account', async () => {
    let resolveFirst;
    const first = new Promise((resolve) => { resolveFirst = resolve; });
    notifications.mockReturnValueOnce(first).mockResolvedValue({ notifications: [], unread_count: 0 });
    const view = renderLayout();
    auth = { token: 'token-b', user: { username: 'student-b', name: 'Student B', role: 'student' }, logout: vi.fn() };
    view.rerender(<MemoryRouter><Routes><Route element={<AppLayout />}><Route index element={<div>Dashboard</div>} /></Route></Routes></MemoryRouter>);
    await waitFor(() => expect(notifications).toHaveBeenCalledTimes(2));
    resolveFirst({ notifications: [unread], unread_count: 1 });
    await openDrawer();
    await waitFor(() => expect(screen.getByText('No notifications')).toBeInTheDocument());
    expect(screen.queryByText('Attendance alert')).not.toBeInTheDocument();
  });
});
