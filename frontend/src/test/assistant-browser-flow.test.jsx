import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import LoginPage from '../pages/auth/LoginPage';
import AssistantPage from '../pages/shared/AssistantPage';

const routing = {
  tier: 'lexicon', margin: 3, tau: 0, escalated: false,
  attempted_llm: false, accepted_llm: false, deterministic_fallback: false,
  reason: 'deterministic personal intent', fallback_from: null,
};

function jsonResponse(body) {
  return {
    ok: true, status: 200, statusText: 'OK',
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => body,
  };
}

describe('student assistant browser flow', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => vi.unstubAllGlobals());

  it('logs in, opens /assistant, and posts deterministic attendance without an unhandled rejection', async () => {
    const unhandled = vi.fn();
    window.addEventListener('unhandledrejection', unhandled);
    const fetch = vi.fn(async (url) => {
      if (url === '/api/auth/login') return jsonResponse({
        token: 'student-token', ai_mode: 'lexicon',
        user: { id: 1, username: 'student.one', role: 'student', name: 'Good Student' },
      });
      if (url === '/api/assistant/capabilities') return jsonResponse({
        role: 'student', title: 'Student academic assistant', subtitle: 'Student scope',
        description: 'Ask about your records.', greeting: 'Hello Good Student.', help: 'Student help.',
        input_placeholder: 'Ask about your academic information…',
        record_capabilities: [], suggestion_groups: [],
      });
      if (url === '/api/chat') return jsonResponse({
        text: 'Overall attendance: 82%', category: 'personal_record',
        source_label: 'Deterministic answer', context_topic: 'attendance',
        mode: 'lexicon', routing, tools_used: [],
      });
      throw new Error(`Unexpected test URL: ${url}`);
    });
    vi.stubGlobal('fetch', fetch);

    render(
      <MemoryRouter initialEntries={['/login']}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/student" element={<Link to="/assistant">Open assistant</Link>} />
            <Route path="/assistant" element={<AssistantPage />} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText(/user id/i), { target: { value: 'student.one' } });
    fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: 'student-password' } });
    fireEvent.click(screen.getByRole('button', { name: /sign in to student/i }));
    fireEvent.click(await screen.findByRole('link', { name: 'Open assistant' }));
    await screen.findByText('Hello Good Student.');

    fireEvent.change(screen.getByLabelText(/ask a question/i), {
      target: { value: 'show me my attendance status' },
    });
    fireEvent.click(screen.getByRole('button', { name: /send message/i }));

    expect(await screen.findByText('Overall attendance: 82%')).toBeInTheDocument();
    const chatCall = fetch.mock.calls.find(([url]) => url === '/api/chat');
    expect(chatCall).toBeTruthy();
    expect(JSON.parse(chatCall[1].body)).toEqual({ message: 'show me my attendance status' });
    await waitFor(() => expect(unhandled).not.toHaveBeenCalled());
    window.removeEventListener('unhandledrejection', unhandled);
  });
});
