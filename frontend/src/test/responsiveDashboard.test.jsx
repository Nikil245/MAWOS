import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ChartCard } from '../components/ChartCard';
import { DashboardCard, DataTable, StatCard } from '../components/ui';
import MarksPerformance from '../pages/student/MarksPerformance';

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children, minWidth }) => <div data-testid="responsive-container" data-min-width={minWidth}>{children}</div>,
  BarChart: ({ children }) => <div>{children}</div>,
  Bar: () => null,
  LineChart: ({ children }) => <div>{children}</div>,
  Line: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
  PieChart: ({ children }) => <div>{children}</div>,
  Pie: ({ children }) => <div>{children}</div>,
  Cell: () => null,
  Legend: () => null,
  ReferenceLine: () => null,
}));

describe('responsive dashboard primitives', () => {
  it('allows long KPI values to shrink and wrap beside a non-shrinking icon', () => {
    const { container } = render(<StatCard label="Outstanding fees for Computer Science and Engineering" value="₹123,456,789,012" detail="A very long department and fee description" />);
    expect(container.querySelector('.card')).toHaveClass('min-w-0');
    expect(container.querySelector('.stat-card-content')).toHaveClass('min-w-0');
    expect(container.querySelector('.stat-card-value')).toHaveClass('break-words');
    expect(container.querySelector('.stat-card-detail')).toHaveClass('break-words');
  });

  it('wraps dashboard headings and keeps wide data inside a focusable table scroller', () => {
    const { container } = render(<DashboardCard title="A deliberately long dashboard section heading"><DataTable columns={[{ key: 'department', label: 'Department' }]} rows={[{ department: 'Computer Science and Engineering' }]} /></DashboardCard>);
    expect(container.querySelector('.dashboard-card-header')).toHaveClass('flex-wrap');
    const scroller = screen.getByLabelText('Scrollable data table');
    expect(scroller).toHaveClass('table-scroll');
    expect(scroller).toHaveAttribute('tabindex', '0');
    expect(scroller.querySelector('table')).toHaveClass('min-w-[640px]');
  });

  it('sizes shared and student charts to their card instead of imposing a phone-width minimum', () => {
    const marks = [{ subject: 'CS401', name: 'Distributed Systems', internals: { 'CIE-1': 40 }, assessment_details: { 'CIE-1': { marks: 40, max_marks: 50 } } }];
    const { container } = render(<><ChartCard title="Department attendance" data={[{ name: 'AIML', value: 82 }]} /><MarksPerformance marks={marks} semester={4} /></>);
    expect(screen.getAllByTestId('responsive-container')).toEqual(expect.arrayContaining([
      expect.objectContaining({ dataset: expect.objectContaining({ minWidth: '0' }) }),
    ]));
    expect(container.querySelector('[data-testid="marks-performance-chart"]')).toHaveClass('min-w-0');
    expect(container.querySelector('[data-testid="marks-performance-chart"]')).not.toHaveClass('min-w-[620px]');
  });
});
