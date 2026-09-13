import { Building2, CircleDollarSign, GraduationCap, UsersRound } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { api } from '../../services/api';
import { useApi } from '../../hooks/useApi';
import { ChartCard } from '../../components/ChartCard';
import { DashboardCard, DataTable, EmptyState, ErrorState, LoadingSkeleton, PageHeader, StatCard, StatusBadge } from '../../components/ui';
import { CampusEventsCard } from '../shared/CampusEvents';

const isRecord = (value) => value && typeof value === 'object' && !Array.isArray(value);
const isFiniteNumber = (value) => typeof value === 'number' && Number.isFinite(value);

export function principalDashboardModel(data) {
  if (!isRecord(data) || !Array.isArray(data.departments)) {
    throw new Error('Principal analytics returned an invalid department collection.');
  }
  if (!data.departments.every((dept) => isRecord(dept) && typeof dept.dept === 'string'
    && isFiniteNumber(dept.students) && isFiniteNumber(dept.faculty)
    && isFiniteNumber(dept.avg_attendance) && isFiniteNumber(dept.shortage_students))) {
    throw new Error('Principal analytics contained an invalid department record.');
  }

  const departments = data.departments;
  const totals = departments.reduce((acc, dept) => ({
    students: acc.students + dept.students,
    faculty: acc.faculty + dept.faculty,
  }), { students: 0, faculty: 0 });
  const attendance = departments.length
    ? Math.round(departments.reduce((sum, dept) => sum + dept.avg_attendance, 0) / departments.length)
    : 0;
  const finance = isRecord(data.fee_collection) && isFiniteNumber(data.fee_collection.total_collected)
    && isFiniteNumber(data.fee_collection.total_outstanding) ? data.fee_collection : null;
  const placements = isRecord(data.placements) && isFiniteNumber(data.placements.eligible_finalists)
    ? data.placements : null;
  const admissionStages = isRecord(data.admissions) && isRecord(data.admissions.stages)
    && Object.values(data.admissions.stages).every(isFiniteNumber) ? data.admissions.stages : null;

  return { departments, totals, attendance, finance, placements, admissionStages };
}

export default function PrincipalDashboard() {
  const { token } = useAuth();
  const { data, loading, error } = useApi(() => api.principalAnalytics(token), [token]);
  if (loading) return <LoadingSkeleton rows={6} />;
  if (error) return <ErrorState error={error} />;

  let model;
  try {
    model = principalDashboardModel(data);
  } catch (contractError) {
    return <ErrorState error={contractError} />;
  }
  const { departments, totals, attendance, finance, placements, admissionStages } = model;

  return <>
    <PageHeader title="Institution overview" eyebrow="Principal portal / Strategic intelligence">
      <p className="mt-1 text-sm text-muted">Institution-wide data for planning, readiness, and department comparison.</p>
    </PageHeader>
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      <StatCard label="Total students" value={totals.students} icon={GraduationCap} />
      <StatCard label="Total faculty" value={totals.faculty} icon={UsersRound} tone="teal" />
      <StatCard label="Overall attendance" value={`${attendance}%`} icon={Building2} tone={attendance < 75 ? 'amber' : 'green'} />
      <StatCard label="Eligible finalists" value={placements?.eligible_finalists ?? '—'} detail={placements ? `${placements.upcoming_drives} upcoming drive(s)` : 'Placement metrics unavailable: invalid API response.'} icon={CircleDollarSign} tone={placements ? 'green' : 'amber'} />
    </div>
    <div className="mt-4 grid gap-4 xl:grid-cols-2">
      <CampusEventsCard className="xl:col-span-2" />
      <ChartCard title="Department attendance comparison" data={departments.map((dept) => ({ name: dept.dept, value: dept.avg_attendance }))} />
      {admissionStages ? <ChartCard title="Admissions funnel" type="donut" data={Object.entries(admissionStages).map(([name, value]) => ({ name, value }))} /> : <DashboardCard title="Admissions funnel"><p role="status" className="text-sm text-red-700">Admissions metrics are unavailable because the API response is incomplete.</p></DashboardCard>}
      <DashboardCard title="Department readiness" className="xl:col-span-2">
        {departments.length ? <DataTable rows={departments} columns={[
          { key: 'dept', label: 'Department' },
          { key: 'students', label: 'Students' },
          { key: 'faculty', label: 'Faculty' },
          { label: 'Attendance', render: (dept) => `${dept.avg_attendance}%` },
          { label: 'Shortage', render: (dept) => <StatusBadge status={dept.shortage_students ? 'warning' : 'success'}>{dept.shortage_students} students</StatusBadge> },
        ]} /> : <EmptyState title="No department analytics available" detail="Department data will appear when it is available from the API." />}
      </DashboardCard>
      <DashboardCard title="Financial overview">
        {finance ? <><p className="text-2xl font-bold">₹{finance.total_collected.toLocaleString()}</p><p className="mt-1 text-sm text-muted">Fee collection recorded by Finance Agent</p><p className="mt-4 text-sm">Outstanding: <b>₹{finance.total_outstanding.toLocaleString()}</b></p></> : <p role="status" className="text-sm text-red-700">Financial metrics are unavailable because the API response is incomplete.</p>}
      </DashboardCard>
      <DashboardCard title="Institution controls"><p className="text-sm text-muted">NAAC/NBA, DPDP, API quota, latency, and library details are not exposed by current FastAPI routes. This page deliberately does not infer those values.</p></DashboardCard>
    </div>
  </>;
}
