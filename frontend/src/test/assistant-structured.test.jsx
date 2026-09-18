import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import AssistantResponse, { safeAssistantBlocks } from '../components/AssistantResponse';

const blocks = [
  { type: 'text', heading: 'Explanation', content: 'A concise educational explanation.' },
  { type: 'metric_cards', title: 'Metrics', cards: [{ label: 'Overall', value: '82%', tone: 'success' }] },
  { type: 'table', title: 'Subjects', caption: 'Subject attendance table', columns: [{ key: 'subject', label: 'Subject' }], rows: [{ subject: 'DBMS' }] },
  { type: 'bullet_list', title: 'Next steps', items: ['Review the example'] },
  { type: 'status_notice', title: 'Eligible', message: 'Requirements are met.', status: 'success' },
  { type: 'empty_state', title: 'No alerts', message: 'Nothing needs attention.' },
  { type: 'link_action', label: 'Open catalogue', url: '/student/library', external: false },
  { type: 'warning', title: 'Attendance warning', message: 'One subject is below threshold.' },
  { type: 'placement_cards', title: 'Placements', cards: [{ company: 'Example Corp', role: 'Engineer', package: '8 LPA', eligibility: 'Eligible', apply_url: 'https://careers.example.com/apply' }] },
  { type: 'library_book_cards', title: 'Books', books: [{ title: 'Python Crash Course', author: 'Eric Matthes', category: 'Python', available_copies: 4, total_copies: 6, availability: 'Available', isbn: '9780000000101' }] },
  { type: 'event_cards', title: 'Events', events: [{ title: 'Tech Fest', date: '2026-09-20', venue: 'Auditorium' }] },
  { type: 'timetable', title: 'Timetable', entries: [{ day: 'Monday', period: '1', subject: 'DBMS', time: '09:00', room: 'A-101' }] },
  { type: 'marks_summary', title: 'Marks', subjects: [{ subject_code: '23AI51', subject_name: 'Machine Learning', internals: { 'CIE-1': 22 }, cie_average: 22 }] },
  { type: 'attendance_summary', overall_percentage: 82, threshold_percentage: 75, subjects: [{ subject_code: '23AI52', subject_name: 'DBMS', attended: 20, total: 24, percentage: 83.3, status: 'On track' }] },
];

function view(role = 'faculty', overrides = {}) {
  const onSuggestion = vi.fn();
  const message = {
    summary: 'Your authorized result is ready.', blocks,
    suggestions: ['Show my timetable', 'Show campus events'],
    source: 'Deterministic MAWOS result',
    safeTrace: {
      visibility: 'collapsible', summary: 'Safe routing and authorization details', duration_ms: 12,
      steps: [{ label: 'Authorization', detail: 'Verified for the authenticated scope', status: 'complete' }],
    },
    ...overrides,
  };
  const rendered = render(<MemoryRouter><AssistantResponse message={message} role={role} onSuggestion={onSuggestion} /></MemoryRouter>);
  return { ...rendered, onSuggestion };
}

describe('structured Operations Assistant blocks', () => {
  it('renders every supported block type without raw JSON or HTML injection', () => {
    view();
    for (const value of [
      'Explanation', '82%', 'DBMS', 'Review the example', 'Requirements are met.',
      'Nothing needs attention.', 'Open catalogue', 'Attendance warning', 'Example Corp',
      'Python Crash Course', 'Tech Fest', 'Machine Learning', '83.3% · On track',
    ]) expect(screen.getAllByText(value, { exact: false }).length).toBeGreaterThan(0);
    expect(screen.getByRole('table', { name: 'Subject attendance table' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open catalogue' })).toHaveAttribute('href', '/student/library');
    expect(screen.getByRole('link', { name: /safe application link/i })).toHaveAttribute('rel', 'noreferrer');
    expect(document.querySelector('[dangerouslySetInnerHTML]')).toBeNull();
    expect(screen.queryByText(/"type":/)).not.toBeInTheDocument();
  });

  it('shows source, keyboard buttons, and contextual suggestion chips', () => {
    const { onSuggestion } = view();
    expect(screen.getByText('Deterministic MAWOS result')).toBeInTheDocument();
    const chip = screen.getByRole('button', { name: 'Show my timetable' });
    chip.focus();
    expect(chip).toHaveFocus();
    fireEvent.click(chip);
    expect(onSuggestion).toHaveBeenCalledWith('Show my timetable');
  });

  it('enforces role-based safe trace visibility and responsive table wrappers', () => {
    const faculty = view('faculty');
    const details = screen.getByText('How this answer was prepared').closest('details');
    expect(details).toHaveClass('hidden', 'md:block');
    faculty.unmount();

    view('student', { safeTrace: { visibility: 'simple', summary: 'Verified against your authorized MAWOS records.', duration_ms: 2, steps: [] } });
    expect(screen.getByText('Verified against your authorized MAWOS records.')).toBeInTheDocument();
    expect(screen.queryByText('How this answer was prepared')).not.toBeInTheDocument();
    expect(document.querySelector('.overflow-x-auto')).toBeTruthy();
  });

  it('rejects malformed blocks and unsafe links instead of partially rendering them', () => {
    expect(safeAssistantBlocks([{ type: 'html', content: '<img onerror=alert(1)>' }])).toBeNull();
    expect(safeAssistantBlocks([{ type: 'link_action', label: 'Unsafe', url: 'javascript:alert(1)' }])).toBeNull();
    expect(safeAssistantBlocks([{ type: 'table', caption: 'Bad', columns: [{ key: 'name', label: 'Name' }], rows: [{ secret: 'value' }] }])).toBeNull();
  });

  it('renders deterministic analytics once with metric cards and a scoped table', () => {
    view('hod', {
      summary: 'The average attendance for first-year AIML students is 78.4%.',
      blocks: [
        { type: 'metric_cards', title: 'Authorized analytics', cards: [
          { label: 'Department', value: 'AIML', tone: 'info' },
          { label: 'Students included', value: '60', tone: 'neutral' },
          { label: 'Average attendance', value: '78.4%', tone: 'success' },
          { label: 'Students below 75%', value: '12', tone: 'danger' },
        ] },
        { type: 'table', title: 'Analytics breakdown', caption: 'Aggregate values within the authenticated scope',
          columns: [{ key: 'semester', label: 'Semester' }, { key: 'average', label: 'Average attendance' }],
          rows: [{ semester: '1', average: '79.0%' }, { semester: '2', average: '77.8%' }] },
      ],
      suggestions: ['Show attendance risk by semester', 'Show student count in my department', 'Show average marks for first year'],
    });

    expect(screen.getAllByText('The average attendance for first-year AIML students is 78.4%.')).toHaveLength(1);
    expect(screen.getByText('Students below 75%')).toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'Aggregate values within the authenticated scope' })).toBeInTheDocument();
  });
});
