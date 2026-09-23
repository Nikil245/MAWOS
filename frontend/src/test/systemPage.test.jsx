import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import SystemPage from '../pages/shared/SystemPage';

const api = vi.hoisted(() => ({ metrics: vi.fn(), agents: vi.fn(), workflows: vi.fn(), workflow: vi.fn() }));
vi.mock('../context/AuthContext', () => ({ useAuth: () => ({ token: 'admin-token' }) }));
vi.mock('../services/api', () => ({ api }));

const workflow = { workflow_id: '123e4567-e89b-12d3-a456-426614174000', title: 'Fee eligibility scan',
  triggering_agent: 'finance_agent', status: 'failed', started_at: '2026-09-23T10:00:00Z',
  completed_at: '2026-09-23T10:00:01Z', duration_ms: 12, event_count: 2 };

describe('system workflow history', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.metrics.mockResolvedValue({ bus_events_logged: 2 });
    api.agents.mockResolvedValue({ ai_mode: 'lexicon', agents: [{ name: 'finance_agent', description: 'Fees' }] });
    api.workflows.mockResolvedValue({ workflows: [workflow] });
    api.workflow.mockResolvedValue({ ...workflow, events: [
      { sequence: 1, agent: 'finance_agent', action: 'Fee eligibility scan', status: 'completed', timestamp: '2026-09-23T10:00:00Z', duration_ms: 4, summary: 'Fee eligibility scan · newly flagged: 3', failure_message: null },
      { sequence: 2, agent: 'notification_agent', action: 'Agent handler failure', status: 'failed', timestamp: '2026-09-23T10:00:01Z', duration_ms: 8, summary: 'An agent handler failed during this workflow.', failure_message: 'An agent handler failed.' },
    ] });
  });

  it('uses readable workflow metadata and shows a safe selected timeline', async () => {
    render(<SystemPage />);
    expect(await screen.findByText('Fee eligibility scan')).toBeInTheDocument();
    expect(screen.queryByText(workflow.workflow_id)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Fee eligibility scan/ }));
    expect(await screen.findByLabelText('Workflow event timeline')).toBeInTheDocument();
    expect(screen.getByText('1. Fee eligibility scan')).toBeInTheDocument();
    expect(screen.getByText('Failure: An agent handler failed.')).toBeInTheDocument();
    expect(screen.getByText(`Workflow ID: ${workflow.workflow_id}`)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Fee eligibility scan/ })).toHaveAttribute('aria-pressed', 'true');
  });

  it('shows a clear empty state', async () => {
    api.workflows.mockResolvedValue({ workflows: [] });
    render(<SystemPage />);
    expect(await screen.findByText('No workflow history')).toBeInTheDocument();
  });
});
