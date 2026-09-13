import { useState } from "react";
import {
  CalendarCheck,
  CreditCard,
  Download,
  GraduationCap,
  Trophy,
} from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { api } from "../../services/api";
import { LibrarySummaryCard } from "../library/Library";
import {
  DashboardCard,
  ErrorState,
  LoadingSkeleton,
  PageHeader,
  StatCard,
  StatusBadge,
  Toast,
} from "../../components/ui";
import { ChartCard } from "../../components/ChartCard";
import { useApi } from "../../hooks/useApi";
import MarksPerformance from "./MarksPerformance";
import { CampusEventsCard } from "../shared/CampusEvents";

const reasonCode = (reasons = "") =>
  reasons.split(/[,;]|\band\b/i).map((reason, index) => (
    <li key={index}>
      <b>R{index + 1}:</b> {reason.trim()}
    </li>
  ));

export default function StudentDashboard() {
  const { token } = useAuth();
  const [revision, setRevision] = useState(0);
  const [toast, setToast] = useState("");
  const { data, loading, error } = useApi(
    () => api.studentDashboard(token),
    [token, revision],
  );
  const pay = async (fee) => {
    try {
      const result = await api.payFee(token, fee.id);
      setToast(
        result.ok
          ? `Payment of ₹${result.paid.toLocaleString()} recorded.`
          : result.error,
      );
      setRevision((v) => v + 1);
    } catch (err) {
      setToast(err.message);
    }
  };
  if (loading) return <LoadingSkeleton rows={7} />;
  if (error)
    return <ErrorState error={error} retry={() => setRevision((v) => v + 1)} />;
  const {
    profile,
    attendance,
    fees,
    hall_ticket: hallTicket,
    scholarship,
    placements,
    marks,
    notifications,
    exams,
  } = data;
  const unpaid = fees.items.filter((fee) => fee.status !== "paid");
  const scholarshipDetail =
    scholarship?.state === "ELIGIBLE"
      ? `${scholarship.eligible_count} eligible opportunities`
      : scholarship?.state === "APPLIED"
        ? "Application submitted"
        : scholarship?.state === "UNABLE_TO_DETERMINE"
          ? "Information needed to determine eligibility"
          : scholarship?.state === "NOT_ELIGIBLE"
            ? "Published opportunities are not currently eligible"
            : scholarship?.state === "CLOSED"
              ? "All scholarships are closed"
              : "No available scholarships";
  const scholarshipValue =
    scholarship?.opportunity?.name ||
    (scholarship?.state === "ELIGIBLE"
      ? "Eligible scholarships"
      : "Scholarships");
  return (
    <>
      <PageHeader
        title={`Hello, ${profile.name || "Student"}`}
        eyebrow={`Student portal / ${profile.dept} · Semester ${profile.semester}`}
        actions={
          hallTicket?.eligible ? (
            <button
              className="btn-secondary"
              onClick={() =>
                setToast(
                  "Hall-ticket download is not exposed by the current API.",
                )
              }
            >
              <Download size={16} />
              Download hall ticket
            </button>
          ) : null
        }
      >
        <p className="mt-1 text-sm text-muted">
          USN {profile.usn} · Year {profile.year} · Section {profile.section}
        </p>
      </PageHeader>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Overall attendance"
          value={`${attendance.overall}%`}
          detail="Minimum requirement: 75%"
          icon={CalendarCheck}
          tone={attendance.overall >= 75 ? "green" : "red"}
        />
        <StatCard
          label="Exam eligibility"
          value={hallTicket?.eligible ? "Eligible" : "Blocked"}
          icon={GraduationCap}
          tone={hallTicket?.eligible ? "green" : "red"}
        />
        <StatCard
          label="Outstanding fees"
          value={`₹${fees.total_outstanding.toLocaleString()}`}
          detail={
            unpaid.length
              ? `${unpaid.length} pending item(s)`
              : "All fees cleared"
          }
          icon={CreditCard}
          tone={unpaid.length ? "amber" : "green"}
        />
        <StatCard
          label="Scholarship"
          value={scholarshipValue}
          detail={scholarshipDetail}
          icon={Trophy}
          tone={
            scholarship?.state === "ELIGIBLE"
              ? "green"
              : scholarship?.state === "UNABLE_TO_DETERMINE"
                ? "amber"
                : "blue"
          }
        />
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <DashboardCard title="Attendance by subject" className="xl:col-span-2">
          <div className="space-y-4">
            {attendance.subjects.map((subject) => (
              <div key={subject.subject}>
                <div className="mb-1 flex justify-between text-sm">
                  <span className="font-medium">{subject.subject}</span>
                  <span
                    className={
                      subject.pct < 75
                        ? "font-semibold text-red-600"
                        : "font-semibold text-emerald-600"
                    }
                  >
                    {subject.attended}/{subject.held} · {subject.pct}%
                  </span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                  <div
                    className={
                      subject.pct < 75 ? "h-full bg-red-500" : "h-full bg-teal"
                    }
                    style={{ width: `${Math.min(subject.pct, 100)}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </DashboardCard>
        <DashboardCard title="Exam eligibility">
          <StatusBadge status={hallTicket?.eligible ? "eligible" : "blocked"}>
            {hallTicket?.eligible ? "ELIGIBLE" : "ACTION REQUIRED"}
          </StatusBadge>
          <ul className="mt-4 space-y-2 text-sm text-muted">
            {hallTicket?.reasons ? (
              reasonCode(hallTicket.reasons)
            ) : (
              <li>All requirements have been met.</li>
            )}
          </ul>
        </DashboardCard>
        <DashboardCard title="Fee status">
          <div className="text-2xl font-bold">
            ₹{fees.total_outstanding.toLocaleString()}
          </div>
          <p className="mt-1 text-sm text-muted">Outstanding balance</p>
          <div className="mt-3 space-y-2">
            {unpaid.length ? (
              unpaid.map((fee) => (
                <div
                  className="flex items-center justify-between gap-2 text-sm"
                  key={fee.id}
                >
                  <span>
                    {fee.type}
                    {fee.fine ? ` · fine ₹${fee.fine}` : ""}
                  </span>
                  <button
                    className="btn-secondary !px-2 !py-1"
                    onClick={() => pay(fee)}
                  >
                    Pay ₹{fee.amount_due.toLocaleString()}
                  </button>
                </div>
              ))
            ) : (
              <p className="text-sm text-emerald-700">
                All payments are up to date.
              </p>
            )}
          </div>
        </DashboardCard>
        <MarksPerformance marks={marks} semester={profile.semester} />
        <DashboardCard title="Placement drives">
          <div className="space-y-3">
            {placements.slice(0, 3).map((drive) => (
              <div
                className="flex items-center justify-between gap-2 border-b pb-3 last:border-0"
                key={drive.company}
              >
                <div>
                  <p className="text-sm font-semibold">{drive.company}</p>
                  <p className="text-xs text-muted">
                    {drive.role} · {drive.package_lpa} LPA
                  </p>
                </div>
                <StatusBadge status={drive.eligible ? "eligible" : "info"}>
                  {drive.eligible ? "Eligible" : "View only"}
                </StatusBadge>
              </div>
            ))}
          </div>
        </DashboardCard>
        <LibrarySummaryCard data={data.library} />
        <CampusEventsCard />
        <DashboardCard title="Internal marks" className="xl:col-span-2">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b text-left text-xs uppercase text-muted">
                <tr>
                  <th className="p-2">Subject</th>
                  <th>CIE 1</th>
                  <th>CIE 2</th>
                  <th>CIE 3</th>
                  <th>Average</th>
                </tr>
              </thead>
              <tbody>
                {marks.map((mark) => (
                  <tr className="border-b" key={mark.subject}>
                    <td className="p-2 font-medium">
                      {mark.subject}
                      <span className="block text-xs font-normal text-muted">
                        {mark.name}
                      </span>
                    </td>
                    <td>{mark.internals["CIE-1"] ?? "—"}</td>
                    <td>{mark.internals["CIE-2"] ?? "—"}</td>
                    <td>{mark.internals["CIE-3"] ?? "—"}</td>
                    <td className="font-semibold">{mark.cie_average ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </DashboardCard>
        <DashboardCard title="Upcoming exams">
          <div className="space-y-2">
            {exams.slice(0, 4).map((exam) => (
              <div className="flex justify-between text-sm" key={exam.subject}>
                <span>{exam.subject}</span>
                <span className="text-muted">{exam.date}</span>
              </div>
            ))}
          </div>
        </DashboardCard>
        <ChartCard
          title="Academic progress"
          type="line"
          data={attendance.subjects.map((s) => ({
            name: s.subject,
            value: s.pct,
          }))}
        />
        <DashboardCard title="Notifications" className="xl:col-span-2">
          <div className="space-y-3">
            {notifications.length ? (
              notifications.slice(0, 5).map((note, i) => (
                <div className="border-b pb-3 last:border-0" key={note.id || i}>
                  <p className="text-sm font-semibold">{note.title}</p>
                  <p className="mt-1 text-sm text-muted">{note.message}</p>
                </div>
              ))
            ) : (
              <p className="text-sm text-muted">No notifications yet.</p>
            )}
          </div>
        </DashboardCard>
      </div>
      <p className="mt-6 text-center text-xs text-muted">
        Student data is aggregated by the Student Agent.
      </p>
      <Toast
        message={toast}
        type={toast.startsWith("Payment") ? "success" : "error"}
        onClose={() => setToast("")}
      />
    </>
  );
}
