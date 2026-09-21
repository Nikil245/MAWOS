import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LibrarySlip, LibrarySummaryCard, LibrarianLibrary, LibrarianManagement, StudentLibrary } from '../pages/library/Library';
import { RoleRoute } from '../components/routes';
import { defaultRouteByRole, isRouteAllowedForRole } from '../routes/roleRoutes';

const mocks = vi.hoisted(() => ({ request: vi.fn(), download: vi.fn(), role: 'student' }));
vi.mock('../context/AuthContext', () => ({ useAuth: () => ({ token: 'library-token', user: { role: mocks.role, username: `${mocks.role}.user` } }) }));
vi.mock('../services/api', () => ({ api: { libraryRequest: mocks.request, downloadLibrarySlip: mocks.download } }));
const book = { id: 11, isbn: '9780000000001', title: 'Database Systems', author: 'Reader', category: 'Computing', departments: ['AIML'], total_copies: 2, available_copies: 1, is_active: true, average_rating: 5 };
const due = '2030-09-14T10:00:00+00:00';
const summary = { issued_count: 1, unpaid_total: '10.00', paid_total: '5.00', estimated_overdue_total: '3.00', next_due_at: due, borrowed: [{ title: book.title, status: 'RETURN_PENDING', due_at: due, estimated_fine: '3.00' }] };
const reservation = { id: 21, book_id: 11, title: book.title, status: 'PENDING_PICKUP', pickup_deadline: due, slip_code: '012345', student_name: 'Asha', student_usn: '4MT23AI001' };
const issued = { id: 31, title: book.title, student_usn: '4MT23AI001', status: 'ISSUED', due_at: due, estimated_fine: '3.00' };
const fine = { id: 41, reason: 'Missed library pickup', status: 'UNPAID', amount: '10.00', created_at: due, student_usn: '4MT23AI001' };
const storedSessionValues = () => Array.from(
  { length: sessionStorage.length }, (_, index) => sessionStorage.getItem(sessionStorage.key(index)),
).join('\n');
function defaults(_token, path, body) {
  if (body !== undefined) return Promise.resolve(path === '/student/library/reservations' ? reservation : { ...issued, id: 31 });
  if (path.includes('/slip')) return Promise.resolve(reservation);
  if (path === '/library/books/11') return Promise.resolve(book);
  if (path.includes('/reviews')) return Promise.resolve({ items: [{ id: 51, book_id: 11, rating: 5, comment: '<img src=x onerror=alert(1)>' }], total: 1 });
  if (path.startsWith('/library/books?')) return Promise.resolve({ items: [book], total: 1 });
  if (path.includes('recommendations')) return Promise.resolve({ items: [{ ...book, explanations: ['Relevant to your department'] }] });
  if (path.includes('summary')) return Promise.resolve({ ...summary, pending_pickups: 1, pending_returns: 1, overdue_count: 1 });
  if (path.includes('reservations?') || path.includes('/records/pickups?')) return Promise.resolve({ items: [reservation], total: 1 });
  if (path.includes('/records/returns?')) return Promise.resolve({ items: [{ ...issued, status: 'RETURN_PENDING' }], total: 1 });
  if (path.includes('issues?') || path.includes('/records/overdue?')) return Promise.resolve({ items: [issued], total: 1 });
  if (path.includes('fines?')) return Promise.resolve({ items: [fine], total: 1 });
  return Promise.resolve({ items: [], total: 0 });
}
function student(initialEntry = '/student/library') { return render(<MemoryRouter initialEntries={[initialEntry]}><Routes><Route path="/student/library" element={<StudentLibrary />} /><Route path="/student/library/slips/:reservationId" element={<LibrarySlip />} /></Routes></MemoryRouter>); }

describe('physical library UI', () => {
  beforeEach(() => { sessionStorage.clear(); mocks.role = 'student'; mocks.request.mockReset().mockImplementation(defaults); mocks.download.mockReset().mockResolvedValue(); });

  it('restores a safe catalogue search after route navigation', async () => {
    render(<MemoryRouter initialEntries={['/student/library']}><Routes>
      <Route path="/student/library" element={<><StudentLibrary /><Link to="/other">Leave library</Link></>} />
      <Route path="/other" element={<><p>Another page</p><Link to="/student/library">Return to library</Link></>} />
    </Routes></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText('Search catalogue'), { target: { value: 'Machine Learning' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search', exact: true }));
    await waitFor(() => expect(sessionStorage.length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('link', { name: 'Leave library' }));
    fireEvent.click(screen.getByRole('link', { name: 'Return to library' }));
    expect(await screen.findByLabelText('Search catalogue')).toHaveValue('Machine Learning');
  });

  it('searches, reserves, opens an owned slip and downloads a PDF', async () => {
    student();
    fireEvent.change(await screen.findByLabelText('Search catalogue'), { target: { value: 'Database' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search', exact: true }));
    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith('library-token', expect.stringContaining('q=Database')));
    fireEvent.click(await screen.findByRole('button', { name: 'Reserve', exact: true }));
    expect(await screen.findByText('012345')).toBeInTheDocument();
    expect(mocks.request).toHaveBeenCalledWith('library-token', '/student/library/reservations', { book_id: 11 }, 'POST');
    fireEvent.click(screen.getByRole('button', { name: 'Download PDF slip' }));
    await waitFor(() => expect(mocks.download).toHaveBeenCalledWith('library-token', '21'));
  });

  it('opens an assistant catalogue link with its safe search term applied', async () => {
    student('/student/library?q=Python%20Crash%20Course');
    expect(await screen.findByLabelText('Search catalogue')).toHaveValue('Python Crash Course');
    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith(
      'library-token', expect.stringContaining('q=Python%20Crash%20Course'),
    ));
  });

  it('cancels reservations and requests physical return with a plain text review', async () => {
    student();
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel reservation' }));
    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith('library-token', '/student/library/reservations/21/cancel', {}, 'POST'));
    fireEvent.click(screen.getByRole('button', { name: 'Borrowed books & history' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Request return', exact: true }));
    expect(screen.getByText(/Fines continue until physical return/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Rating (optional)'), { target: { value: '4' } });
    fireEvent.change(screen.getByLabelText('Review (optional)'), { target: { value: 'Helpful reference' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit return request' }));
    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith('library-token', '/student/library/issues/31/return-request', { rating: 4, comment: 'Helpful reference' }, 'POST'));
    fireEvent.click(screen.getByRole('button', { name: 'Fine history' }));
    expect(await screen.findByText('Missed library pickup')).toBeInTheDocument();
    expect(screen.getByText('Pay in person at the library counter.')).toBeInTheDocument();
  });

  it('shows reader comments as inert text and recommendations with explanations', async () => {
    student(); expect(await screen.findByText('Relevant to your department')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: 'Details & reviews' }));
    const dialog = screen.getByRole('dialog', { name: 'Book details and reviews' });
    expect(await within(dialog).findByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
    expect(dialog.querySelector('img')).toBeNull();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close details' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('supports librarian slip verification, pickup, direct issue, return and fine collection', async () => {
    mocks.role = 'librarian'; mocks.request.mockImplementation((token, path, body) => path === '/librarian/library/verify-slip' ? Promise.resolve(reservation) : defaults(token, path, body));
    render(<MemoryRouter><LibrarianLibrary /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText('Six-digit slip code'), { target: { value: '012345' } });
    fireEvent.click(screen.getByRole('button', { name: 'Verify slip' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm verified pickup' }));
    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith('library-token', '/librarian/library/reservations/21/pickup', {}, 'POST'));
    fireEvent.change(screen.getByLabelText('Student USN'), { target: { value: '4MT23AI001' } });
    fireEvent.change(screen.getByLabelText('Book ID'), { target: { value: '11' } });
    fireEvent.click(screen.getByRole('button', { name: 'Issue at counter' }));
    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith('library-token', '/librarian/library/issues', { student_usn: '4MT23AI001', book_id: 11 }, 'POST'));
    fireEvent.click(await screen.findByRole('button', { name: 'Pending returns', exact: true }));
    fireEvent.click(await screen.findByRole('button', { name: 'Reject return', exact: true }));
    fireEvent.change(screen.getByLabelText('Return rejection reason'), { target: { value: 'Not handed over' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save rejection' }));
    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith('library-token', '/librarian/library/issues/31/reject-return', { reason: 'Not handed over' }, 'POST'));
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm physical return' }));
    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith('library-token', '/librarian/library/issues/31/return', {}, 'POST'));
    fireEvent.click(screen.getByRole('button', { name: 'Overdue report' }));
    expect(await screen.findByText(/Estimated fine ₹3.00/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Fine collection' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm cash collected / Mark paid' }));
    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith('library-token', '/librarian/library/fines/41/paid', {}, 'POST'));
  });

  it('makes every librarian KPI a keyboard-accessible operation shortcut', async () => {
    mocks.role = 'librarian';
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: scrollIntoView });
    render(<MemoryRouter><LibrarianLibrary /></MemoryRouter>);
    for (const [kpi, tab] of [['Issued books', 'All issues / Counter returns'], ['Pending pickups', 'Pending pickups'], ['Pending returns', 'Pending returns'], ['Overdue books', 'Overdue report'], ['Unpaid fines', 'Fine collection']]) {
      fireEvent.click(await screen.findByRole('button', { name: `Open ${kpi}` }));
      expect(await screen.findByRole('button', { name: tab, exact: true })).toHaveClass('btn-primary');
    }
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
  });

  it('shows read-only parent book and fine summary without slip or action controls', () => {
    render(<LibrarySummaryCard data={summary} parent />);
    expect(screen.getByText('Database Systems')).toBeInTheDocument();
    expect(screen.getByText('Paid fines: ₹5.00')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByText('012345')).not.toBeInTheDocument();
  });

  it('creates librarian credentials once and deactivates existing accounts', async () => {
    mocks.role = 'admin';
    const librarian = { id: 71, username: 'desk.user', display_name: 'Desk User', active: true };
    mocks.request.mockImplementation((_token, path, body) => Promise.resolve(body ? path === '/admin/librarians' ? { ...librarian, generated_credentials: { username: 'desk.user', temporary_password: 'Temporary-secret-123!' } } : librarian : { items: [librarian], total: 1 }));
    render(<MemoryRouter><LibrarianManagement /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'desk.user' } });
    fireEvent.change(screen.getByLabelText('Display name'), { target: { value: 'Desk User' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create librarian account' }));
    expect(await screen.findByText(/Temporary-secret-123!/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'I have saved them' }));
    expect(screen.queryByText(/Temporary-secret-123!/)).not.toBeInTheDocument();
    expect(storedSessionValues()).not.toContain('Temporary-secret-123!');
    expect(storedSessionValues()).not.toContain('Desk User');
    fireEvent.click(await screen.findByRole('button', { name: 'Deactivate' }));
    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith('library-token', '/admin/librarians/71', { display_name: 'Desk User', active: false }, 'PUT'));
  });

  it('renders loading, empty, retry and mutation validation states', async () => {
    mocks.request.mockImplementation((_token, path) => path.includes('/library/books?') ? Promise.reject(new Error('Catalogue unavailable')) : Promise.resolve(path.includes('summary') ? summary : { items: [], total: 0 }));
    student();
    expect(await screen.findByText('Catalogue unavailable')).toBeInTheDocument();
    expect(screen.getByText('No library records yet')).toBeInTheDocument();
    mocks.request.mockImplementation(defaults);
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('button', { name: 'Reserve', exact: true })).toBeInTheDocument();
    mocks.request.mockImplementation((token, path, body) => body ? Promise.reject(new Error('Book is unavailable')) : defaults(token, path, body));
    fireEvent.click(screen.getByRole('button', { name: 'Reserve', exact: true }));
    expect(await screen.findByText('Book is unavailable')).toBeInTheDocument();
  });

  it('shows a loading state and an empty catalogue', async () => {
    mocks.request.mockImplementation(() => new Promise(() => {}));
    const view = render(<MemoryRouter><LibrarySlip /></MemoryRouter>);
    expect(screen.getByRole('status', { name: 'Loading library data' })).toBeInTheDocument();
    view.unmount();
    mocks.request.mockImplementation((_token, path) => Promise.resolve(path.includes('summary') ? summary : { items: [], total: 0 }));
    student();
    expect(await screen.findByText('No books found')).toBeInTheDocument();
    expect(screen.getByText('No recommendations yet')).toBeInTheDocument();
  });

  it('lets staff add, edit and archive catalogue stock through the API', async () => {
    mocks.role = 'librarian';
    render(<MemoryRouter><LibrarianLibrary /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Add book', exact: true }));
    for (const [label, value] of [['isbn', '9780000000002'], ['title', 'New title'], ['author', 'New author'], ['category', 'Computing'], ['Department codes (comma separated, blank for all)', 'AIML']]) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    }
    expect(storedSessionValues()).not.toContain('New title');
    expect(storedSessionValues()).not.toContain('9780000000002');
    fireEvent.click(screen.getByRole('button', { name: 'Save book' }));
    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith('library-token', '/librarian/library/books', expect.objectContaining({ title: 'New title', departments: ['AIML'], total_copies: 1 }), 'POST'));
    fireEvent.click(await screen.findByRole('button', { name: 'Edit', exact: true }));
    fireEvent.change(screen.getByLabelText('Total copies'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save book' }));
    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith('library-token', '/librarian/library/books/11', expect.objectContaining({ total_copies: 3 }), 'PUT'));
    fireEvent.click(await screen.findByRole('button', { name: 'Archive', exact: true }));
    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith('library-token', '/librarian/library/books/11/archive', {}, 'POST'));
  });

  it('shows Restore only for archived catalogue books and refreshes it to Archive', async () => {
    mocks.role = 'librarian'; let restored = false;
    mocks.request.mockImplementation((token, path, body) => {
      if (path.startsWith('/library/books?')) return Promise.resolve({ items: [{ ...book, is_active: restored }], total: 1 });
      if (path === '/librarian/library/books/11/unarchive') { restored = true; return Promise.resolve({ ...book, is_active: true }); }
      return defaults(token, path, body);
    });
    render(<MemoryRouter><LibrarianLibrary /></MemoryRouter>);
    expect(await screen.findByRole('button', { name: 'Restore', exact: true })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Archive', exact: true })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Restore', exact: true }));
    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith('library-token', '/librarian/library/books/11/unarchive', {}, 'POST'));
    expect(await screen.findByText('Book restored.')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Archive', exact: true })).toBeInTheDocument();
  });

  it('opens the borrow and reserve control with an available recommendation preselected', async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: scrollIntoView });
    student();
    fireEvent.click(await screen.findByRole('button', { name: 'Reserve recommended Database Systems' }));
    expect(await screen.findByRole('region', { name: 'Selected book for reservation' })).toHaveTextContent('Database Systems');
    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith('library-token', '/library/books/11'));
    fireEvent.click(screen.getByRole('button', { name: 'Reserve selected book' }));
    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith('library-token', '/student/library/reservations', { book_id: 11 }, 'POST'));
    expect(scrollIntoView).toHaveBeenCalled();
  });

  it('does not initiate a reservation flow for an unavailable recommendation', async () => {
    mocks.request.mockImplementation((token, path, body) => path.includes('recommendations')
      ? Promise.resolve({ items: [{ ...book, available_copies: 0, explanations: ['Relevant to your department'] }] })
      : defaults(token, path, body));
    student();
    fireEvent.click(await screen.findByRole('button', { name: 'Reserve recommended Database Systems' }));
    expect(await screen.findByText('This recommended book is currently unavailable.')).toBeInTheDocument();
    expect(mocks.request.mock.calls.some(([, path]) => path === '/library/books/11')).toBe(false);
  });

  it('clears an invalid direct book selection without exposing a reservation action', async () => {
    mocks.request.mockImplementation((token, path, body) => path === '/library/books/999'
      ? Promise.reject(new Error('Not found')) : defaults(token, path, body));
    student('/student/library?book=999');
    expect(await screen.findByText('Selected book is unavailable or no longer accessible.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reserve selected book' })).not.toBeInTheDocument();
  });

  it('protects actual librarian routes and safe role return paths', async () => {
    expect(defaultRouteByRole.librarian).toBe('/librarian/library');
    expect(isRouteAllowedForRole('student', '/student/library/slips/21')).toBe(true);
    for (const role of ['student', 'parent', 'faculty', 'hod', 'principal']) expect(isRouteAllowedForRole(role, '/librarian/library')).toBe(false);
    expect(isRouteAllowedForRole('librarian', '/admin/librarians')).toBe(false);
    render(<MemoryRouter initialEntries={['/librarian/library']}><Routes><Route element={<RoleRoute roles={['librarian']} />}><Route path="/librarian/library" element={<p>Private desk</p>} /></Route><Route path="/student" element={<p>Student overview</p>} /></Routes></MemoryRouter>);
    expect(await screen.findByText('Student overview')).toBeInTheDocument();
    expect(screen.queryByText('Private desk')).not.toBeInTheDocument();
  });
});
