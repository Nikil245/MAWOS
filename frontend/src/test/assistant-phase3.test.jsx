import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { BrowserRouter } from 'react-router-dom';
import AssistantPage from '../pages/shared/AssistantPage';
import { api } from '../services/api';

const auth = vi.hoisted(() => ({ token: 'first-token', user: { id: 1, role: 'student', name: 'First' }, checking: false }));
vi.mock('../context/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('../services/api', () => ({
  api: { chat: vi.fn(), assistantCapabilities: vi.fn() },
  ApiError: class ApiError extends Error { constructor(message, status, category) { super(message); this.status = status; this.category = category; } },
  isAbortError: (error) => error?.name === 'AbortError',
}));
const routing = { tier: 'scope', attempted_llm: true, accepted_llm: true };
const answer = (text, topic = 'fees', label = 'Deterministic answer') => ({
  text, context_topic: topic, source_label: label, category: 'personal_record', mode: 'lexicon', routing,
});
const generalAnswer = (text) => ({
  text, context_topic: null, source_label: 'General AI response', category: 'general_ai',
  mode: 'general_ai', routing: { ...routing, tier: 'llm' }, tools_used: [],
});
function capabilityFor(role, name) {
  const ui = {
    student: ['Student academic assistant', 'My records', 'What is my attendance?', 'Student scope.'],
    faculty: ['Faculty academic assistant', 'Assigned students', 'Show attendance for [authorized student USN].', 'Faculty scope.'],
    hod: ['HOD academic assistant', 'Department records', 'Show attendance for [student USN in your department].', 'HOD scope.'],
    principal: ['Principal academic assistant', null, null, 'Principal scope.'],
    admin: ['Admin academic assistant', 'Supported records', 'Show attendance for [student USN].', 'Admin scope.'],
  }[role];
  const recordGroup = ui[1] ? [{ label: ui[1], prompts: [ui[2]] }] : [];
  const libraryGroup = role === 'student' ? [{ label: 'Library catalogue', prompts: [
    'Find available AIML books',
    'Recommend a Python book from the library',
    "Check a book's availability",
  ] }] : [];
  return {
    role,
    title: ui[0], subtitle: `${role} authorized scope`, description: ui[3], help: `${role} help`,
    greeting: `Hello ${name}. ${ui[3]}`,
    input_placeholder: `Ask within ${role} scope…`,
    record_capabilities: [],
    suggestion_groups: [
      ...recordGroup,
      ...libraryGroup,
      { label: 'MAWOS help', prompts: ['What is a CIE?'] },
      { label: 'General learning', prompts: ['Explain machine learning simply.'] },
      { label: 'Assistant help', prompts: ['What can you help me with?'] },
    ],
  };
}
async function mount() {
  const view = render(<BrowserRouter><AssistantPage /></BrowserRouter>);
  await screen.findByText(capabilityFor(auth.user.role, auth.user.name).greeting);
  return view;
}
function send(text) {
  fireEvent.change(screen.getByLabelText(/ask a question/i), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: /send message/i }));
}

describe('Phase 3 assistant privacy and labels', () => {
  beforeEach(() => {
    vi.clearAllMocks(); localStorage.clear(); sessionStorage.clear();
    auth.token = 'first-token'; auth.user = { id: 1, role: 'student', name: 'First' }; auth.checking = false;
    api.assistantCapabilities.mockImplementation(async () => capabilityFor(auth.user.role, auth.user.name));
  });

  it('groups backend suggested prompts and allows selecting one', async () => {
    await mount();
    for (const label of ['My records', 'Library catalogue', 'MAWOS help', 'General learning', 'Assistant help']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole('button', { name: 'What is a CIE?' }));
    expect(screen.getByLabelText(/ask a question/i)).toHaveValue('What is a CIE?');
    expect(api.chat).not.toHaveBeenCalled();
  });

  it('waits for authentication restoration before requesting capabilities', async () => {
    auth.checking = true;
    const view = render(<BrowserRouter><AssistantPage /></BrowserRouter>);
    expect(screen.getByText('Preparing your assistant…')).toBeInTheDocument();
    expect(api.assistantCapabilities).not.toHaveBeenCalled();

    auth.checking = false;
    view.rerender(<BrowserRouter><AssistantPage /></BrowserRouter>);
    await screen.findByText('Hello First. Student scope.');
    expect(api.assistantCapabilities).toHaveBeenCalledTimes(1);
  });

  it('retries one initial authentication race after the account is ready', async () => {
    api.assistantCapabilities
      .mockRejectedValueOnce({ category: 'authentication', status: 401 })
      .mockResolvedValueOnce(capabilityFor('student', 'First'));
    await mount();
    expect(api.assistantCapabilities).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/temporarily unavailable/i)).not.toBeInTheDocument();
  });

  it.each([new Error('network unavailable'), { category: 'server', status: 500 }])(
    'shows safe retry UI for a recoverable capability failure', async (failure) => {
      api.assistantCapabilities
        .mockRejectedValueOnce(failure)
        .mockResolvedValueOnce(capabilityFor('student', 'First'));
      const view = render(<BrowserRouter><AssistantPage /></BrowserRouter>);
      expect(await screen.findByText(/capabilities are temporarily unavailable/i)).toBeInTheDocument();
      expect(screen.queryByText('My records')).not.toBeInTheDocument();
      expect(screen.getByText('General help')).toBeInTheDocument();
      expect(screen.queryByText('network unavailable')).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
      await screen.findByText('Hello First. Student scope.');
      expect(screen.queryByText(/temporarily unavailable/i)).not.toBeInTheDocument();
      view.unmount();
    },
  );

  it('rejects malformed capability data without showing role record prompts', async () => {
    api.assistantCapabilities.mockResolvedValue({ role: 'student', greeting: 'untrusted' });
    render(<BrowserRouter><AssistantPage /></BrowserRouter>);
    expect(await screen.findByText(/capabilities are temporarily unavailable/i)).toBeInTheDocument();
    expect(screen.queryByText('My records')).not.toBeInTheDocument();
  });

  it('does not turn StrictMode aborts into a capability error', async () => {
    api.assistantCapabilities.mockImplementation((_token, signal) => new Promise((resolve, reject) => {
      const timer = setTimeout(() => resolve(capabilityFor('student', 'First')), 0);
      signal.addEventListener('abort', () => {
        clearTimeout(timer);
        const error = new Error('cancelled'); error.name = 'AbortError'; reject(error);
      });
    }));
    render(<StrictMode><BrowserRouter><AssistantPage /></BrowserRouter></StrictMode>);
    await screen.findByText('Hello First. Student scope.');
    expect(screen.queryByText(/temporarily unavailable/i)).not.toBeInTheDocument();
  });

  it('ignores a slow previous-account capability response', async () => {
    let resolveFirst;
    api.assistantCapabilities
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce(capabilityFor('faculty', 'Second'));
    const view = render(<BrowserRouter><AssistantPage /></BrowserRouter>);
    auth.token = 'second-token'; auth.user = { id: 2, role: 'faculty', name: 'Second' };
    view.rerender(<BrowserRouter><AssistantPage /></BrowserRouter>);
    await screen.findByText('Hello Second. Faculty scope.');
    await act(async () => { resolveFirst(capabilityFor('student', 'First')); });
    expect(screen.queryByText('Hello First. Student scope.')).not.toBeInTheDocument();
    expect(screen.getByText('Assigned students')).toBeInTheDocument();
  });

  it.each([
    ['student', 'Student academic assistant', 'My records'],
    ['faculty', 'Faculty academic assistant', 'Assigned students'],
    ['hod', 'HOD academic assistant', 'Department records'],
    ['principal', 'Principal academic assistant', null],
    ['admin', 'Admin academic assistant', 'Supported records'],
  ])('renders backend capabilities for the %s role', async (role, title, recordGroup) => {
    auth.user = { id: role, role, name: `${role} user` };
    await mount();
    expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    expect(screen.getByText('General learning')).toBeInTheDocument();
    if (recordGroup) expect(screen.getByText(recordGroup)).toBeInTheDocument();
    expect(screen.queryByText(role === 'student' ? 'Assigned students' : 'My records')).not.toBeInTheDocument();
  });

  it.each(['Deterministic answer', 'AI-grounded record answer', 'General AI response',
    'Official MAWOS information', 'Safe fallback', 'Clarification',
    'Library catalogue result', 'Library-guided AI response'])(
    'renders the backend source label: %s', async (label) => {
      api.chat.mockResolvedValue(answer('Example response', 'cie', label));
      await mount(); send('What is a CIE?');
      expect(await screen.findByText(label)).toBeInTheDocument();
    },
  );

  it('renders general AI separately with an accuracy warning and escaped text', async () => {
    api.chat.mockResolvedValue(generalAnswer('<script>window.pwned = true</script> Binary search halves the range.'));
    await mount(); send('Explain binary search.');
    expect(await screen.findByText('General AI response')).toBeInTheDocument();
    expect(screen.getByText(/Generated by the local AI model and may contain mistakes/)).toBeInTheDocument();
    expect(document.querySelector('script')).toBeNull();
    expect(window.pwned).toBeUndefined();
  });

  it('renders student library quick prompts and a safe internal catalogue action', async () => {
    api.chat.mockResolvedValue({
      ...answer('Python Crash Course — 4 of 6 copies available.', null, 'Library catalogue result'),
      category: 'library_catalogue',
      actions: [{ label: 'Open Library catalogue', route: '/student/library?q=Python%20Crash%20Course' }],
    });
    await mount();
    expect(screen.getByRole('button', { name: 'Find available AIML books' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Recommend a Python book from the library' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: "Check a book's availability" })).toBeInTheDocument();
    send('Is Python Crash Course available?');
    expect(await screen.findByText('Library catalogue result')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open Library catalogue' })).toHaveAttribute(
      'href', '/student/library?q=Python%20Crash%20Course',
    );
    expect(screen.queryByText(/may contain mistakes/)).not.toBeInTheDocument();
  });

  it('renders an unavailable general response as a safe fallback without an AI accuracy badge', async () => {
    api.chat.mockResolvedValue({
      ...generalAnswer('The local AI model is temporarily unavailable.'),
      mode: 'scope', source_label: 'Safe fallback', fallback: true, fallback_code: 'deadline_exceeded',
    });
    await mount(); send('Explain binary search.');
    expect(await screen.findByText('Safe fallback')).toBeInTheDocument();
    expect(screen.getByText(/temporarily unavailable/)).toBeInTheDocument();
    expect(screen.queryByText(/may contain mistakes/)).not.toBeInTheDocument();
  });

  it('sends only bounded general history and never adds a record response', async () => {
    api.chat.mockResolvedValueOnce(generalAnswer('First general answer.'))
      .mockResolvedValueOnce(answer('Private record answer.'))
      .mockResolvedValueOnce(generalAnswer('General follow-up answer.'));
    await mount();
    send('Explain binary search.'); await screen.findByText('First general answer.');
    send('Show my fees'); await screen.findByText('Private record answer.');
    send('Compare that with linear search.'); await screen.findByText('General follow-up answer.');
    expect(api.chat.mock.calls[2][4]).toEqual([
      { role: 'user', content: 'Explain binary search.' },
      { role: 'assistant', content: 'First general answer.' },
    ]);
    expect(JSON.stringify(api.chat.mock.calls[2][4])).not.toContain('Private record answer');
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
  });

  it('clears general history when the authenticated account changes', async () => {
    api.chat.mockResolvedValueOnce(generalAnswer('First user general answer.'))
      .mockResolvedValueOnce(generalAnswer('Second user answer.'));
    const view = await mount();
    send('Explain a stack.'); await screen.findByText('First user general answer.');
    auth.token = 'second-token'; auth.user = { id: 2, role: 'faculty', name: 'Second' };
    view.rerender(<BrowserRouter><AssistantPage /></BrowserRouter>);
    await screen.findByText('Hello Second. Faculty scope.');
    send('Explain a queue.'); await screen.findByText('Second user answer.');
    expect(api.chat.mock.calls[1][4]).toEqual([]);
  });

  it('sends one topic only, clears it on clarification, and persists nothing', async () => {
    api.chat.mockResolvedValueOnce(answer('Your recorded fees'))
      .mockResolvedValueOnce(answer('Please clarify', null, 'Clarification'))
      .mockResolvedValueOnce(answer('No prior topic', null));
    await mount(); send('Show my fees');
    await screen.findByText('Your recorded fees');
    send('Why?'); await screen.findByText('Please clarify');
    expect(api.chat.mock.calls[1]).toEqual(['first-token', 'Why?', expect.any(AbortSignal), 'fees', []]);
    send('Why?'); await screen.findByText('No prior topic');
    expect(api.chat.mock.calls[2][3]).toBeNull();
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
  });

  it('clears messages and context on account switch and ignores a late reply', async () => {
    let resolveOld;
    api.chat.mockResolvedValueOnce(answer('First account fees'))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce(answer('Second account clarification', null));
    const view = await mount(); send('Show my fees'); await screen.findByText('First account fees');
    send('Why?');
    const signal = api.chat.mock.calls[1][2];
    auth.token = 'second-token'; auth.user = { id: 2, role: 'faculty', name: 'Second' };
    view.rerender(<BrowserRouter><AssistantPage /></BrowserRouter>);
    await screen.findByText('Hello Second. Faculty scope.');
    expect(signal.aborted).toBe(true);
    expect(screen.queryByText('First account fees')).not.toBeInTheDocument();
    expect(screen.queryByText('My records')).not.toBeInTheDocument();
    expect(screen.getByText('Assigned students')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show attendance for [authorized student USN].' })).toBeInTheDocument();
    await act(async () => { resolveOld(answer('Late private reply')); });
    expect(screen.queryByText('Late private reply')).not.toBeInTheDocument();
    send('Why?'); await screen.findByText('Second account clarification');
    expect(api.chat.mock.calls[2]).toEqual(['second-token', 'Why?', expect.any(AbortSignal), null, []]);
  });

  it('clears context on logout and remount, even for the same account', async () => {
    api.chat.mockResolvedValue(answer('Record answer'));
    const view = await mount(); send('Show my fees'); await screen.findByText('Record answer');
    auth.token = null; auth.user = null;
    view.rerender(<BrowserRouter><AssistantPage /></BrowserRouter>);
    expect(screen.queryByText('Record answer')).not.toBeInTheDocument();
    auth.token = 'first-token'; auth.user = { id: 1, role: 'student', name: 'First' };
    view.rerender(<BrowserRouter><AssistantPage /></BrowserRouter>);
    await screen.findByText('Hello First. Student scope.');
    send('Why?'); await screen.findByText('Record answer');
    expect(api.chat.mock.calls[1][3]).toBeNull();
    view.unmount(); await mount(); send('Why?'); await screen.findByText('Record answer');
    expect(api.chat.mock.calls[2][3]).toBeNull();
  });

  it('bounds displayed history to twenty messages', async () => {
    let count = 0;
    api.chat.mockImplementation(async () => answer(`Reply ${++count}`));
    await mount();
    for (let i = 0; i < 12; i += 1) {
      send(`Question ${i}`); await screen.findByText(`Reply ${i + 1}`);
    }
    expect(screen.queryByText('Reply 1')).not.toBeInTheDocument();
    expect(screen.getByText('Reply 12')).toBeInTheDocument();
    await waitFor(() => expect(api.chat).toHaveBeenCalledTimes(12));
  });
});
