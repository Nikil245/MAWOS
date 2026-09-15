import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { BrowserRouter, Link, MemoryRouter, Route, Routes } from 'react-router-dom';
import AssistantPage from '../pages/shared/AssistantPage';
import { api } from '../services/api';
import { clearAllUserSessionState, userSessionKey } from '../hooks/useUserSessionState';

const auth = vi.hoisted(() => ({ token: 'first-token', user: { id: 1, role: 'student', name: 'First' }, checking: false }));
vi.mock('../context/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('../services/api', () => ({
  api: { chat: vi.fn(), assistantCapabilities: vi.fn() },
  ApiError: class ApiError extends Error { constructor(message, status, category) { super(message); this.status = status; this.category = category; } },
  isAbortError: (error) => error?.name === 'AbortError',
}));
const routing = { tier: 'scope', attempted_llm: true, accepted_llm: true };
const contextProof = 'a'.repeat(64);
const contextIssuedAt = 1_700_000_000;
const answer = (text, topic = 'fees', label = 'Deterministic answer') => ({
  text, context_topic: topic, source_label: label, category: 'personal_record', mode: 'lexicon', routing,
});
const generalAnswer = (text) => ({
  text, context_topic: null, source_label: 'General AI response', category: 'general_ai',
  mode: 'general_ai', routing: { ...routing, tier: 'llm' }, tools_used: [], context_proof: contextProof,
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
  await screen.findByPlaceholderText(`Ask within ${auth.user.role} scope…`);
  return view;
}
function send(text) {
  fireEvent.change(screen.getByLabelText(/ask a question/i), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: /send message/i }));
}
const storedSessionValues = () => Array.from(
  { length: sessionStorage.length }, (_, index) => sessionStorage.getItem(sessionStorage.key(index)),
).join('\n');

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

  it.each([
    ['Deterministic answer', 'Deterministic MAWOS result'],
    ['AI-grounded record answer', 'Deterministic MAWOS result'],
    ['General AI response', 'Generated by local AI'],
    ['Official MAWOS information', 'Deterministic MAWOS result'],
    ['Safe fallback', 'Safe fallback'],
    ['Clarification', 'Clarification'],
    ['Library catalogue result', 'Deterministic MAWOS result'],
    ['Library-guided AI response', 'Generated by local AI'],
    ['Generated by Groq AI', 'Generated by Groq AI'],
    ['Generated by local AI', 'Generated by local AI'],
  ])(
    'renders the honest source label for %s', async (label, displayed) => {
      api.chat.mockResolvedValue(answer('Example response', 'cie', label));
      await mount(); send('What is a CIE?');
      expect(await screen.findByText(displayed)).toBeInTheDocument();
    },
  );

  it('renders general AI separately with an accuracy warning and escaped text', async () => {
    api.chat.mockResolvedValue(generalAnswer('<script>window.pwned = true</script> Binary search halves the range.'));
    await mount(); send('Explain binary search.');
    expect(await screen.findByText('Generated by local AI')).toBeInTheDocument();
    expect(screen.getByText(/Generated by AI and may contain mistakes/)).toBeInTheDocument();
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
    expect(await screen.findByText('Deterministic MAWOS result')).toBeInTheDocument();
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
      { role: 'user', category: 'general_ai', content: 'Explain binary search.' },
      { role: 'assistant', category: 'general_ai', content: 'First general answer.', proof: contextProof },
    ]);
    expect(JSON.stringify(api.chat.mock.calls[2][4])).not.toContain('Private record answer');
    expect(localStorage.length).toBe(0);
    expect(JSON.parse(sessionStorage.getItem(userSessionKey(auth.user, 'assistant'))).value.conversationContext).toEqual([
      { role: 'user', category: 'general_ai', content: 'Explain binary search.' },
      { role: 'assistant', category: 'general_ai', content: 'First general answer.', proof: contextProof },
      { role: 'user', category: 'general_ai', content: 'Compare that with linear search.' },
      { role: 'assistant', category: 'general_ai', content: 'General follow-up answer.', proof: contextProof },
    ]);
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

  it('sends one topic only and clears it on clarification', async () => {
    api.chat.mockResolvedValueOnce(answer('Your recorded fees'))
      .mockResolvedValueOnce(answer('Please clarify', null, 'Clarification'))
      .mockResolvedValueOnce(answer('No prior topic', null));
    await mount(); send('Show my fees');
    await screen.findByText('Your recorded fees');
    send('Why?'); await screen.findByText('Please clarify');
    expect(api.chat.mock.calls[1]).toEqual(['first-token', 'Why?', expect.any(AbortSignal), 'fees', []]);
    send('Why?'); await screen.findByText('No prior topic');
    expect(api.chat.mock.calls[2][3]).toBeNull();
    expect(localStorage.length).toBe(0);
    expect(storedSessionValues()).not.toContain('first-token');
  });

  it('never restores User A history for User B and ignores User A late replies', async () => {
    let resolveOld;
    api.chat.mockResolvedValueOnce(answer('First account fees'))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce(answer('Second account clarification', null));
    const view = await mount(); send('Show my fees'); await screen.findByText('First account fees');
    await waitFor(() => expect(sessionStorage.getItem(userSessionKey(auth.user, 'assistant'))).toContain('First account fees'));
    send('Why?');
    const signal = api.chat.mock.calls[1][2];
    auth.token = 'second-token'; auth.user = { id: 2, role: 'faculty', name: 'Second' };
    view.rerender(<BrowserRouter><AssistantPage /></BrowserRouter>);
    await screen.findByText('Hello Second. Faculty scope.');
    expect(signal.aborted).toBe(true);
    expect(screen.queryByText('First account fees')).not.toBeInTheDocument();
    expect(storedSessionValues()).not.toContain('first-token');
    expect(screen.queryByText('My records')).not.toBeInTheDocument();
    expect(screen.getByText('Assigned students')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show attendance for [authorized student USN].' })).toBeInTheDocument();
    await act(async () => { resolveOld(answer('Late private reply')); });
    expect(screen.queryByText('Late private reply')).not.toBeInTheDocument();
    send('Why?'); await screen.findByText('Second account clarification');
    expect(api.chat.mock.calls[2]).toEqual(['second-token', 'Why?', expect.any(AbortSignal), null, []]);
  });

  it('clears context on logout before starting a newly persistent session', async () => {
    api.chat.mockResolvedValue(answer('Record answer'));
    const view = await mount(); send('Show my fees'); await screen.findByText('Record answer');
    auth.token = null; auth.user = null;
    clearAllUserSessionState();
    view.rerender(<BrowserRouter><AssistantPage /></BrowserRouter>);
    expect(screen.queryByText('Record answer')).not.toBeInTheDocument();
    auth.token = 'first-token'; auth.user = { id: 1, role: 'student', name: 'First' };
    view.rerender(<BrowserRouter><AssistantPage /></BrowserRouter>);
    await screen.findByText('Hello First. Student scope.');
    send('Why?'); await screen.findByText('Record answer');
    expect(api.chat.mock.calls[1][3]).toBeNull();
    view.unmount(); await mount(); send('Why?'); await screen.findByText('Record answer');
    expect(api.chat.mock.calls[2][3]).toBe('fees');
  });

  it('bounds displayed history to fifty completed messages', async () => {
    let count = 0;
    api.chat.mockImplementation(async () => answer(`Reply ${++count}`));
    await mount();
    for (let i = 0; i < 27; i += 1) {
      send(`Question ${i}`); await screen.findByText(`Reply ${i + 1}`);
    }
    expect(screen.queryByText('Reply 1')).not.toBeInTheDocument();
    expect(screen.getByText('Reply 27')).toBeInTheDocument();
    await waitFor(() => expect(api.chat).toHaveBeenCalledTimes(27));
  });

  it('restores completed history after route navigation', async () => {
    api.chat.mockResolvedValueOnce(generalAnswer('A Java roadmap starts with syntax.'))
      .mockResolvedValueOnce(generalAnswer('Learn syntax first.'));
    render(<MemoryRouter initialEntries={['/assistant']}><Routes>
      <Route path="/assistant" element={<><AssistantPage /><Link to="/other">Open other page</Link></>} />
      <Route path="/other" element={<><p>Other page</p><Link to="/assistant">Back to assistant</Link></>} />
    </Routes></MemoryRouter>);
    await screen.findByText('Hello First. Student scope.');
    send('Keep this question');
    await screen.findByText('A Java roadmap starts with syntax.');
    fireEvent.click(screen.getByRole('link', { name: 'Open other page' }));
    expect(screen.getByText('Other page')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('link', { name: 'Back to assistant' }));
    expect(await screen.findByText('A Java roadmap starts with syntax.')).toBeInTheDocument();
    expect(screen.getByText('Keep this question')).toBeInTheDocument();
    send('What should I learn first?');
    await screen.findByText('Learn syntax first.');
    expect(api.chat.mock.calls[1][4]).toEqual([
      { role: 'user', category: 'general_ai', content: 'Keep this question' },
      { role: 'assistant', category: 'general_ai', content: 'A Java roadmap starts with syntax.', proof: contextProof },
    ]);
  });

  it('restores completed history after a same-tab refresh remount', async () => {
    api.chat.mockResolvedValueOnce(generalAnswer('Refresh-safe answer'))
      .mockResolvedValueOnce(generalAnswer('Context-aware answer'));
    const first = await mount();
    send('Keep this through refresh');
    await screen.findByText('Refresh-safe answer');
    await waitFor(() => expect(sessionStorage.getItem(userSessionKey(auth.user, 'assistant'))).toContain('Refresh-safe answer'));
    first.unmount();
    await mount();
    expect(screen.getByText('Refresh-safe answer')).toBeInTheDocument();
    expect(screen.getByText('Keep this through refresh')).toBeInTheDocument();
    send('Explain this more simply.');
    await screen.findByText('Context-aware answer');
    expect(api.chat.mock.calls[1][4]).toEqual([
      { role: 'user', category: 'general_ai', content: 'Keep this through refresh' },
      { role: 'assistant', category: 'general_ai', content: 'Refresh-safe answer', proof: contextProof },
    ]);
  });

  it('sends only the latest four completed context pairs', async () => {
    api.chat.mockImplementation(async (_token, text) => generalAnswer(`Reply to ${text}`));
    await mount();
    for (let index = 0; index < 5; index += 1) {
      send(`General question ${index}`);
      await screen.findByText(`Reply to General question ${index}`);
    }
    send('What should I learn next?');
    await screen.findByText('Reply to What should I learn next?');
    const sent = api.chat.mock.calls[5][4];
    expect(sent).toHaveLength(8);
    expect(JSON.stringify(sent)).not.toContain('General question 0');
    expect(JSON.stringify(sent)).toContain('General question 1');
  });

  it('persists and sends only safe canonical library references', async () => {
    api.chat.mockResolvedValueOnce({
      ...answer('Python Crash Course is available.', null, 'Library catalogue result'),
      category: 'library_catalogue', context_books: [
        { title: 'Python Crash Course', isbn: '9780000000101', author: 'Eric Matthes', category: 'Python' },
      ], context_proof: contextProof, context_issued_at: contextIssuedAt,
      data: { books: [{ title: 'Python Crash Course', available_copies: 4, internal_id: 99 }] },
    }).mockResolvedValueOnce({
      ...answer('Eric Matthes wrote it.', null, 'Library catalogue result'),
      category: 'library_catalogue', context_books: [],
      context_proof: contextProof,
    });
    await mount();
    send('Find Python Crash Course');
    await screen.findByText('Python Crash Course is available.');
    send('Who wrote it?');
    await screen.findByText('Eric Matthes wrote it.');
    expect(api.chat.mock.calls[1][4]).toEqual([
      { role: 'user', category: 'library_catalogue', content: 'Find Python Crash Course' },
      { role: 'assistant', category: 'library_catalogue', content: 'Python Crash Course is available.',
        proof: contextProof, issued_at: contextIssuedAt, books: [
          { title: 'Python Crash Course', isbn: '9780000000101', author: 'Eric Matthes', category: 'Python' },
        ] },
    ]);
    expect(JSON.stringify(api.chat.mock.calls[1][4])).not.toContain('available_copies');
    expect(storedSessionValues()).not.toContain('internal_id');
  });

  it('preserves the exact three-book recommendation contract for a follow-up', async () => {
    const javaCandidates = [
      { title: 'Effective Java', isbn: '9780134685991', author: 'Joshua Bloch', category: 'Java' },
      { title: 'Eloquent JavaScript', isbn: '9781718504103', author: 'Marijn Haverbeke', category: 'JavaScript' },
      { title: 'Head First Java', isbn: '9781491910771', author: 'Kathy Sierra', category: 'Java' },
    ];
    api.chat.mockResolvedValueOnce({
      ...answer('Three grounded Java catalogue recommendations.', null, 'Library-guided AI response'),
      category: 'library_catalogue', mode: 'llm', context_books: javaCandidates,
      context_proof: contextProof, context_issued_at: contextIssuedAt,
    }).mockResolvedValueOnce({
      ...answer('Best choice: Effective Java, because it improves practical Java design.', null,
        'Library catalogue result'),
      category: 'library_catalogue', context_books: javaCandidates,
      context_proof: contextProof, context_issued_at: contextIssuedAt,
    });
    await mount();
    send('recommend a Java book');
    await screen.findByText('Three grounded Java catalogue recommendations.');
    expect(screen.getByRole('list', { name: 'Library candidates' })).toBeInTheDocument();
    expect(screen.getByText('1. Effective Java')).toBeInTheDocument();
    expect(screen.getByText('2. Eloquent JavaScript')).toBeInTheDocument();
    expect(screen.getByText('3. Head First Java')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Which is best for a beginner?' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Compare these books' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Check availability' })).toBeInTheDocument();
    expect(screen.queryByText(contextProof)).not.toBeInTheDocument();

    send('which book is best among the three books');
    await screen.findByText(/Best choice: Effective Java/);
    expect(api.chat.mock.calls[1][4]).toEqual([
      { role: 'user', category: 'library_catalogue', content: 'recommend a Java book' },
      { role: 'assistant', category: 'library_catalogue',
        content: 'Three grounded Java catalogue recommendations.', proof: contextProof,
        issued_at: contextIssuedAt, books: javaCandidates },
    ]);
  });

  it('does not expose a previous account candidate set after switching users', async () => {
    const candidates = [
      { title: 'Effective Java', isbn: '9780134685991', author: 'Joshua Bloch', category: 'Java' },
      { title: 'Head First Java', isbn: '9781491910771', author: 'Kathy Sierra', category: 'Java' },
    ];
    api.chat.mockResolvedValueOnce({
      ...answer('Two Java candidates.', null, 'Library catalogue result'),
      category: 'library_catalogue', context_books: candidates,
      context_proof: contextProof, context_issued_at: contextIssuedAt,
    }).mockResolvedValueOnce(generalAnswer('No prior candidate context.'));
    const view = await mount();
    send('recommend a Java book');
    await screen.findByText('Two Java candidates.');
    expect(screen.getByRole('button', { name: 'Compare these books' })).toBeInTheDocument();
    auth.token = 'second-token'; auth.user = { id: 2, role: 'faculty', name: 'Second' };
    view.rerender(<BrowserRouter><AssistantPage /></BrowserRouter>);
    await screen.findByText('Hello Second. Faculty scope.');
    expect(screen.queryByText('Two Java candidates.')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Compare these books' })).not.toBeInTheDocument();
    send('which book is best among the two');
    await screen.findByText('No prior candidate context.');
    expect(api.chat.mock.calls[1][4]).toEqual([]);
  });

  it('discards malformed stored history safely', async () => {
    const key = userSessionKey(auth.user, 'assistant');
    sessionStorage.setItem(key, '{"value":[malformed');
    await mount();
    expect(screen.getByText('Hello First. Student scope.')).toBeInTheDocument();
    expect(screen.queryByText(/malformed/i)).not.toBeInTheDocument();
  });

  it('still sends attendance after malformed stored context without an unhandled rejection', async () => {
    const unhandled = vi.fn();
    window.addEventListener('unhandledrejection', unhandled);
    const key = userSessionKey(auth.user, 'assistant');
    sessionStorage.setItem(key, JSON.stringify({
      version: 1, owner: { identifier: '1', role: 'student' }, expiresAt: Date.now() + 60000,
      value: {
        messages: [], contextTopic: 'attendance',
        conversationContext: [{ role: 'assistant', content: 'broken' }],
      },
    }));
    api.chat.mockResolvedValue(answer('Overall attendance: 82%', 'attendance'));

    await mount();
    send('show me my attendance status');

    expect(await screen.findByText('Overall attendance: 82%')).toBeInTheDocument();
    expect(api.chat).toHaveBeenCalledWith(
      'first-token', 'show me my attendance status', expect.any(AbortSignal), null, [],
    );
    expect(unhandled).not.toHaveBeenCalled();
    window.removeEventListener('unhandledrejection', unhandled);
  });

  it('discards expired stored history safely', async () => {
    const key = userSessionKey(auth.user, 'assistant');
    sessionStorage.setItem(key, JSON.stringify({
      version: 1, owner: { identifier: '1', role: 'student' }, expiresAt: Date.now() - 1,
      value: { messages: [
        { id: 'stored-0', role: 'user', text: 'Expired question' },
        { id: 'stored-1', role: 'agent', text: 'Expired answer' },
      ], contextTopic: null, conversationContext: [] },
    }));
    await mount();
    expect(screen.queryByText('Expired answer')).not.toBeInTheDocument();
    expect(screen.getByText('Hello First. Student scope.')).toBeInTheDocument();
  });

  it('posts attendance without expired stored context', async () => {
    const key = userSessionKey(auth.user, 'assistant');
    sessionStorage.setItem(key, JSON.stringify({
      version: 1, owner: { identifier: '1', role: 'student' }, expiresAt: Date.now() - 1,
      value: { messages: [], contextTopic: 'fees', conversationContext: [] },
    }));
    api.chat.mockResolvedValue(answer('Attendance with expired context discarded', 'attendance'));
    await mount();

    send('show me my attendance status');

    await screen.findByText('Attendance with expired context discarded');
    expect(api.chat).toHaveBeenCalledWith(
      'first-token', 'show me my attendance status', expect.any(AbortSignal), null, [],
    );
  });

  it('posts attendance with tampered signed context for server-side rejection', async () => {
    const key = userSessionKey(auth.user, 'assistant');
    const tamperedContext = [
      { role: 'user', category: 'general_ai', content: 'Explain SQL.' },
      { role: 'assistant', category: 'general_ai', content: 'Tampered answer.', proof: contextProof },
    ];
    sessionStorage.setItem(key, JSON.stringify({
      version: 1, owner: { identifier: '1', role: 'student' }, expiresAt: Date.now() + 60000,
      value: { messages: [], contextTopic: null, conversationContext: tamperedContext },
    }));
    api.chat.mockResolvedValue(answer('Attendance after signature rejection', 'attendance'));
    await mount();

    send('show me my attendance status');

    await screen.findByText('Attendance after signature rejection');
    expect(api.chat).toHaveBeenCalledWith(
      'first-token', 'show me my attendance status', expect.any(AbortSignal), null, tamperedContext,
    );
  });

  it('rejects an envelope owned by a different user even under the current key', async () => {
    const key = userSessionKey(auth.user, 'assistant');
    sessionStorage.setItem(key, JSON.stringify({
      version: 1, owner: { identifier: '2', role: 'student' }, expiresAt: Date.now() + 60000,
      value: { messages: [
        { id: 'stored-0', role: 'user', text: 'User B private question' },
        { id: 'stored-1', role: 'agent', text: 'User B private answer' },
      ], contextTopic: null, conversationContext: [] },
    }));
    await mount();
    expect(screen.queryByText('User B private answer')).not.toBeInTheDocument();
    expect(screen.getByText('Hello First. Student scope.')).toBeInTheDocument();
  });

  it('clears only the current conversation after confirmation', async () => {
    api.chat.mockResolvedValue(answer('Answer to clear'));
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    sessionStorage.setItem('mawos_ui:v1:faculty:2:assistant', 'other-user-state');
    await mount(); send('Question to clear');
    await screen.findByText('Answer to clear');
    fireEvent.click(screen.getByRole('button', { name: 'Clear conversation' }));
    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('Answer to clear')).not.toBeInTheDocument();
    expect(screen.queryByText('Question to clear')).not.toBeInTheDocument();
    expect(screen.getByText('Hello First. Student scope.')).toBeInTheDocument();
    await waitFor(() => expect(
      JSON.parse(sessionStorage.getItem(userSessionKey(auth.user, 'assistant'))).value.messages,
    ).toEqual([]));
    expect(sessionStorage.getItem('mawos_ui:v1:faculty:2:assistant')).toBe('other-user-state');
  });

  it('sends the next attendance request without context after clearing', async () => {
    api.chat
      .mockResolvedValueOnce(generalAnswer('A prior general answer.'))
      .mockResolvedValueOnce(answer('Attendance after clear: 82%', 'attendance'));
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    await mount();
    send('Explain normalization.');
    await screen.findByText('A prior general answer.');

    fireEvent.click(screen.getByRole('button', { name: 'Clear conversation' }));
    send('show me my attendance status');

    expect(await screen.findByText('Attendance after clear: 82%')).toBeInTheDocument();
    expect(api.chat.mock.calls[1][4]).toEqual([]);
  });
});
