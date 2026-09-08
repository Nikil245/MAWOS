import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = readFileSync('src/pages/student/StudentDashboard.jsx', 'utf8');

describe('student dashboard scholarship summary', () => {
  it('keeps only the top workflow scholarship KPI', () => {
    expect(source).toContain('label="Scholarship"');
    expect(source).not.toContain('DashboardCard title="Scholarship"');
    expect(source).not.toContain('No scholarship assessment is available.');
  });
});
