import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../context/AuthContext';
import AssistantPage from '../pages/shared/AssistantPage';
import { api } from '../services/api';

vi.mock('../services/api', () => ({
  api: { chat: vi.fn(), assistantCapabilities: vi.fn(), me: vi.fn() },
  setUnauthorizedHandler: vi.fn(),
  ApiError: class ApiError extends Error { constructor(message, status, category) { super(message); this.status = status; this.category = category; } },
  isAbortError: (error) => error?.name === 'AbortError',
}));

const routing = {
  tier: 'lexicon', margin: 3, tau: 0, escalated: false,
  attempted_llm: false, accepted_llm: false, deterministic_fallback: false,
  reason: 'margin 3.00 > tau 0.00', fallback_from: null,
};

const capability = {
  role: 'student', title: 'Student academic assistant', subtitle: 'Student scope',
  description: 'You may ask about your own records.', greeting: 'Hello Good Student. Student scope.', help: 'Student help.',
  input_placeholder: 'Ask about your academic information…', record_capabilities: [], suggestion_groups: [],
};

async function renderAssistant() {
  localStorage.setItem('mawos_token', 'token');
  localStorage.setItem('mawos_user', JSON.stringify({
    role: 'student', name: 'Good Student', ai_mode: 'lexicon',
  }));
  const view = render(<BrowserRouter><AuthProvider><AssistantPage /></AuthProvider></BrowserRouter>);
  await screen.findByText(capability.greeting);
  return view;
}

describe('academic assistant response contract', () => {
  beforeEach(() => {
    localStorage.clear(); vi.clearAllMocks();
    api.assistantCapabilities.mockResolvedValue(capability);
  });

  it('renders canonical deterministic text instead of raw response JSON', async () => {
    api.chat.mockResolvedValue({
      text: 'Overall attendance: 82%', mode: 'lexicon', routing,
      tools_used: [{ name: 'get_attendance', args: {}, ms: 1 }], fallback: false,
    });
    await renderAssistant();

    fireEvent.change(screen.getByLabelText(/ask a question/i), { target: { value: 'What is my attendance?' } });
    fireEvent.click(screen.getByRole('button', { name: /send message/i }));

    expect(await screen.findByText('Overall attendance: 82%')).toBeInTheDocument();
    expect(screen.getByText('Deterministic answer')).toBeInTheDocument();
    expect(screen.queryByText(/"text":/)).not.toBeInTheDocument();
  });

  it('waits for delayed AuthContext restoration before loading capabilities', async () => {
    let restore;
    localStorage.setItem('mawos_token', 'restored-token');
    api.me.mockImplementation(() => new Promise((resolve) => { restore = resolve; }));
    const view = render(<BrowserRouter><AuthProvider><AssistantPage /></AuthProvider></BrowserRouter>);
    expect(screen.getByText('Preparing your assistant…')).toBeInTheDocument();
    expect(api.assistantCapabilities).not.toHaveBeenCalled();

    await waitFor(() => expect(restore).toBeTypeOf('function'));
    restore({ role: 'student', name: 'Good Student', username: 'restored' });
    await screen.findByText(capability.greeting);
    expect(api.assistantCapabilities).toHaveBeenCalledTimes(1);
    view.unmount();
  });

  it('labels an actual model answer and its verified fallback state', async () => {
    api.chat.mockResolvedValue({
      text: 'Your fees are cleared.', mode: 'llm', routing: { ...routing, tier: 'llm', escalated: true },
      tools_used: [{ name: 'get_fees', args: {}, ms: 1 }], fallback: true,
    });
    await renderAssistant();

    fireEvent.change(screen.getByLabelText(/ask a question/i), { target: { value: 'Do I owe fees?' } });
    fireEvent.click(screen.getByRole('button', { name: /send message/i }));

    expect(await screen.findByText('Your fees are cleared.')).toBeInTheDocument();
    expect(screen.getByText('Deterministic fallback after Ollama')).toBeInTheDocument();
  });

  it('uses fallback metadata while keeping a normal null-code lexicon answer deterministic', async () => {
    api.chat
      .mockResolvedValueOnce({
        text: 'All fees are cleared.', mode: 'lexicon', routing: {
          ...routing, fallback_from: 'llm', deterministic_fallback: false,
        }, fallback: false, fallback_code: null,
      })
      .mockResolvedValueOnce({
        text: 'Marks unavailable.', mode: 'lexicon', routing: {
          ...routing, escalated: true, attempted_llm: true,
          fallback_from: 'llm', deterministic_fallback: true,
        }, fallback: true, fallback_code: 'grounding_validation_failed',
      });
    await renderAssistant();

    const input = screen.getByLabelText(/ask a question/i);
    fireEvent.change(input, { target: { value: 'Do I owe fees?' } });
    fireEvent.click(screen.getByRole('button', { name: /send message/i }));
    expect(await screen.findByText('All fees are cleared.')).toBeInTheDocument();
    expect(screen.getByText('Deterministic answer')).toBeInTheDocument();

    fireEvent.change(input, { target: { value: 'Show my marks' } });
    fireEvent.click(screen.getByRole('button', { name: /send message/i }));
    expect(await screen.findByText('Marks unavailable.')).toBeInTheDocument();
    expect(screen.getByText('Deterministic fallback')).toBeInTheDocument();
  });

  it('shows loading and a safe error without exposing backend details', async () => {
    let reject;
    api.chat.mockImplementation(() => new Promise((resolve, rejectPromise) => { reject = rejectPromise; }));
    await renderAssistant();

    fireEvent.change(screen.getByLabelText(/ask a question/i), { target: { value: 'Show my marks' } });
    fireEvent.click(screen.getByRole('button', { name: /send message/i }));
    expect(screen.getByText('Looking up your authorized records…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /send message/i })).toBeDisabled();

    reject(new Error('internal database password'));
    await waitFor(() => expect(screen.getByText(/records were not changed/i)).toBeInTheDocument());
    expect(screen.queryByText(/database password/i)).not.toBeInTheDocument();
  });
});
