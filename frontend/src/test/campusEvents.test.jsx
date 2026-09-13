import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AdminCampusEvents, CampusEventsCard } from '../pages/shared/CampusEvents';

const mocks = vi.hoisted(() => ({
  campusEvents: vi.fn(),
  adminCampusEvents: vi.fn(),
  saveCampusEvent: vi.fn(),
  campusEventAction: vi.fn(),
}));

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ token: 'test-token', user: { role: 'student', dept: 'AIML' } }),
}));
vi.mock('../services/api', () => ({ api: mocks }));

const today = {
  id: 1, title: 'Founders Day', description: 'Campus celebration',
  event_date: '2026-09-12', start_time: '10:00:00', end_time: '12:00:00',
  venue: 'Auditorium',
};
const upcoming = {
  id: 2, title: 'Hackathon', description: null,
  event_date: '2026-09-15', start_time: null, end_time: null, venue: 'Lab',
};

describe('campus events UI', () => {
  beforeEach(() => {
    Object.values(mocks).forEach(mock => mock.mockReset());
  });

  it('renders compact no-event states and the view-all action', async () => {
    mocks.campusEvents.mockResolvedValue({ today: [], upcoming: [], events: [] });
    render(<MemoryRouter><CampusEventsCard /></MemoryRouter>);
    expect(await screen.findByRole('heading', { name: 'Campus events' })).toBeInTheDocument();
    expect(screen.getByText('No events scheduled today.')).toBeInTheDocument();
    expect(screen.getByText('No upcoming events.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'View all events' })).toHaveAttribute('href', '/events');
  });

  it('renders all matching today events and sorted upcoming summaries responsively', async () => {
    mocks.campusEvents.mockResolvedValue({ today: [today], upcoming: [upcoming], events: [today, upcoming] });
    const { container } = render(<MemoryRouter><CampusEventsCard /></MemoryRouter>);
    expect(await screen.findByText('Founders Day')).toBeInTheDocument();
    expect(screen.getByText('Hackathon')).toBeInTheDocument();
    expect(screen.getByText('10:00–12:00')).toBeInTheDocument();
    expect(screen.getByText('Auditorium')).toBeInTheDocument();
    expect(container.querySelector('.sm\\:grid-cols-2')).toBeInTheDocument();
  });

  it('submits an admin draft through the protected management API', async () => {
    mocks.adminCampusEvents.mockResolvedValue({ events: [] });
    mocks.saveCampusEvent.mockResolvedValue({ id: 3 });
    render(<MemoryRouter><AdminCampusEvents /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText('Title'), { target: { value: 'Sports Day' } });
    fireEvent.change(screen.getByLabelText('Event date'), { target: { value: '2026-09-20' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create event' }));
    await waitFor(() => expect(mocks.saveCampusEvent).toHaveBeenCalledWith(
      'test-token', null, expect.objectContaining({
        title: 'Sports Day', event_date: '2026-09-20', status: 'DRAFT', audience: 'ALL',
      })));
  });
});
