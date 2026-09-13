import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { api, viewPlacementDocument } from "../../services/api";
import { DashboardCard, PageHeader } from "../../components/ui";

const emptyDrive = {
  company: "",
  role: "",
  package_lpa: "",
  drive_date: "",
  departments: "ALL",
  min_cgpa: 6,
  max_backlogs: 0,
  min_attendance: 75,
  status: "OPEN",
  requires_fee_clearance: false,
  application_deadline: "",
  description: "",
  application_url: "",
};
const editable = ["DRAFT", "OPEN"];
const active = ["OPEN", "SHORTLIST_GENERATED"];
const badge =
  "inline-block rounded-full bg-slate-100 px-2 py-1 text-xs font-semibold";

function Reasons({ value }) {
  return (
    <ul className="list-disc space-y-1 pl-4">
      {(value || "")
        .split("; ")
        .filter(Boolean)
        .map((reason, i) => (
          <li key={i}>{reason}</li>
        ))}
    </ul>
  );
}
function Evaluation({ entry, admin = false }) {
  const evaluated =
    (entry.eligibility_status || entry.status) !== "NOT_EVALUATED";
  return (
    <>
      <span className={badge}>
        {!evaluated
          ? "Not evaluated"
          : entry.eligible
            ? "Eligible"
            : "Not eligible"}
      </span>
      <p className="my-2 text-sm">
        {!evaluated
          ? "Rules-only evaluation"
          : entry.ml_probability != null
            ? `Model evaluated${admin ? ` · ${entry.ml_probability.toFixed(2)}` : ""}`
            : "Rules-only evaluation"}
      </p>
      {admin && (
        <p className="mb-2 text-xs text-muted">
          Model version: {entry.model_version || "—"}
        </p>
      )}
      <Reasons value={entry.reasons} />
    </>
  );
}
function Notice({ error, success }) {
  return (
    <>
      {error && (
        <div
          role="alert"
          className="mb-4 rounded border border-red-200 bg-red-50 p-3"
        >
          {error}
        </div>
      )}
      {success && (
        <p role="status" className="mb-4 text-green-700">
          {success}
        </p>
      )}
    </>
  );
}
function ConfirmModal({ modal, busy, onClose, onConfirm }) {
  const [reason, setReason] = useState("");
  if (!modal) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <div className="w-full max-w-lg rounded bg-white p-5 shadow-xl">
        <h2 id="confirm-title" className="text-lg font-semibold">
          {modal.title}
        </h2>
        <p className="my-3">{modal.message}</p>
        {modal.kind === "cancel" && (
          <label className="block text-sm">
            Cancellation reason
            <textarea
              autoFocus
              required
              className="mt-1 min-h-24 w-full rounded border p-2"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button disabled={busy} className="btn-secondary" onClick={onClose}>
            Go back
          </button>
          <button
            disabled={busy || (modal.kind === "cancel" && !reason.trim())}
            className={
              modal.destructive
                ? "rounded bg-red-700 px-4 py-2 text-white"
                : "btn-primary"
            }
            onClick={() => onConfirm(reason.trim())}
          >
            {modal.confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export function StudentPlacements() {
  const { token, user } = useAuth();
  return user?.role === "student" ? (
    <StudentList key={`${token}:${user.usn}`} token={token} />
  ) : null;
}
function StudentList({ token }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let current = true;
    setRows(null);
    setError("");
    api
      .placementDrives(token)
      .then((data) => current && setRows(data))
      .catch((err) => current && setError(err.message));
    return () => {
      current = false;
    };
  }, [token, retry]);
  return (
    <>
      <PageHeader title="My placements" eyebrow="MAWOS / Careers">
        <p>Recruitment drives and your own eligibility.</p>
      </PageHeader>
      {error ? (
        <div role="alert">
          {error}{" "}
          <button
            className="btn-secondary"
            onClick={() => setRetry((x) => x + 1)}
          >
            Retry
          </button>
        </div>
      ) : !rows ? (
        <p role="status">Loading placements…</p>
      ) : !rows.length ? (
        <p>No placement drives available.</p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {rows.map((drive) => (
            <DashboardCard
              key={drive.id}
              title={`${drive.company} · ${drive.role}`}
            >
              <p className="mb-2">
                ₹{drive.package_lpa} LPA · {drive.drive_date}
              </p>
              <p className="mb-3">
                <span className={badge}>{drive.status}</span>
              </p>
              <Evaluation entry={drive} />
              <a
                className="btn-primary mt-4 inline-block"
                href={`/student/placements/${drive.id}`}
              >
                View details
              </a>
            </DashboardCard>
          ))}
        </div>
      )}
    </>
  );
}

export function StudentPlacementDetail() {
  const { token, user } = useAuth();
  const { driveId } = useParams();
  const [entry, setEntry] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let current = true;
    api
      .placementDrive(token, driveId)
      .then((data) => current && setEntry(data))
      .catch((err) => current && setError(err.message));
    return () => {
      current = false;
    };
  }, [token, user?.usn, driveId]);
  async function openPdf() {
    setBusy(true);
    setError("");
    try {
      await viewPlacementDocument(token, driveId);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  if (error && !entry)
    return (
      <>
        <Link to="/student/placements">← Back to placements</Link>
        <div role="alert" className="mt-4">
          {error}
        </div>
      </>
    );
  if (!entry) return <p role="status">Loading placement details…</p>;
  const d = entry.drive;
  return (
    <>
      <Link to="/student/placements">← Back to placements</Link>
      <PageHeader
        title={`${d.company} · ${d.role}`}
        eyebrow="Placement details"
      >
        <p>
          <span className={badge}>{d.status}</span>
        </p>
      </PageHeader>
      <Notice error={error} />
      <DashboardCard title="Drive information">
        <dl className="grid gap-3 sm:grid-cols-2">
          <div>
            <dt>Package</dt>
            <dd>₹{d.package_lpa} LPA</dd>
          </div>
          <div>
            <dt>Drive date</dt>
            <dd>{d.drive_date}</dd>
          </div>
          <div>
            <dt>Application deadline</dt>
            <dd>{d.application_deadline || "Not specified"}</dd>
          </div>
          <div>
            <dt>Eligible departments</dt>
            <dd>{d.departments}</dd>
          </div>
          <div>
            <dt>Minimum CGPA</dt>
            <dd>{d.min_cgpa}</dd>
          </div>
          <div>
            <dt>Maximum backlogs</dt>
            <dd>{d.max_backlogs}</dd>
          </div>
          <div>
            <dt>Minimum attendance</dt>
            <dd>{d.min_attendance}%</dd>
          </div>
        </dl>
        <h3 className="mt-5 font-semibold">Job / company description</h3>
        <p className="mt-2 whitespace-pre-wrap">
          {d.description || "No description provided."}
        </p>
        {d.cancellation_reason && (
          <p className="mt-4 rounded bg-red-50 p-3">
            <strong>Cancellation reason:</strong> {d.cancellation_reason}
          </p>
        )}
        {d.job_document && (
          <button
            disabled={busy}
            className="btn-secondary mt-4"
            onClick={openPdf}
          >
            View job-description PDF ({d.job_document.original_name})
          </button>
        )}
      </DashboardCard>
      <div className="mt-4">
        <DashboardCard title="Your eligibility">
          <Evaluation entry={entry} />
          {entry.can_apply ? (
            <a
              className="btn-primary mt-4 inline-block"
              href={d.application_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              Apply on company portal
            </a>
          ) : (
            <p className="mt-4 rounded bg-amber-50 p-3">
              {entry.apply_message}
            </p>
          )}
          <p className="mt-2 text-xs text-muted">
            This opens the company portal; MAWOS does not submit an internal
            application.
          </p>
        </DashboardCard>
      </div>
    </>
  );
}

export function AdminPlacements() {
  const { token, user } = useAuth();
  return user?.role === "admin" ? (
    <AdminList key={token} token={token} />
  ) : null;
}
function AdminList({ token }) {
  const [drives, setDrives] = useState(null);
  const [form, setForm] = useState({ ...emptyDrive });
  const [editing, setEditing] = useState(null);
  const [file, setFile] = useState(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busy, setBusy] = useState(false);
  const [modal, setModal] = useState(null);
  const refresh = async () => setDrives(await api.placementDrives(token));
  useEffect(() => {
    refresh().catch((err) => setError(err.message));
  }, [token]);
  async function perform(work) {
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      await work();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  function reset() {
    setEditing(null);
    setForm({ ...emptyDrive });
    setFile(null);
  }
  function editDrive(drive) {
    setEditing(drive.id);
    setForm(
      Object.fromEntries(
        Object.keys(emptyDrive).map((k) => [k, drive[k] ?? emptyDrive[k]]),
      ),
    );
    setFile(null);
  }
  async function persistDrive(body) {
    await perform(async () => {
      const saved = await api.savePlacementDrive(token, editing, body);
      const id = editing || saved.id;
      if (file) await api.uploadPlacementDocument(token, id, file);
      reset();
      await refresh();
      setSuccess("Drive saved.");
    });
  }
  async function saveDrive(event) {
    event.preventDefault();
    const body = {
      ...form,
      company: form.company.trim(),
      role: form.role.trim(),
      departments: form.departments
        .toUpperCase()
        .split(",")
        .map((x) => x.trim())
        .join(","),
      application_deadline: form.application_deadline || null,
      description: form.description.trim() || null,
      application_url: form.application_url.trim() || null,
    };
    for (const key of [
      "package_lpa",
      "min_cgpa",
      "max_backlogs",
      "min_attendance",
    ])
      body[key] = Number(body[key]);
    if (
      editing &&
      file &&
      drives?.find((d) => d.id === editing)?.job_document
    ) {
      setModal({
        kind: "replacePdf",
        title: "Replace job-description PDF?",
        message:
          "The current private PDF will be replaced after the updated drive is saved.",
        confirmLabel: "Replace PDF",
        body,
      });
      return;
    }
    await persistDrive(body);
  }
  function askGenerate(drive) {
    const regenerate =
      drive.status === "SHORTLIST_GENERATED" || drive.candidate_count > 0;
    setModal({
      kind: "generate",
      title: regenerate ? "Regenerate shortlist?" : "Generate shortlist?",
      message: regenerate
        ? "Eligibility will be recalculated. Existing outcomes remain unchanged."
        : "Evaluate final-year students using the existing placement rules?",
      confirmLabel: regenerate ? "Regenerate shortlist" : "Generate shortlist",
      drive,
      regenerate,
    });
  }
  function lifecycle(drive, kind) {
    setModal({
      kind,
      drive,
      title: kind === "close" ? "Close drive?" : "Cancel drive?",
      message:
        kind === "close"
          ? "Closing means recruitment finished normally. Existing data is preserved; new applications and shortlist generation will stop."
          : "Cancellation means this drive was withdrawn. Existing data is preserved, but applications and shortlist generation will stop.",
      confirmLabel: kind === "close" ? "Close drive" : "Cancel drive",
      destructive: kind === "cancel",
    });
  }
  async function confirmModal(reason) {
    const current = modal;
    setModal(null);
    if (current.kind === "replacePdf") {
      await persistDrive(current.body);
      return;
    }
    await perform(async () => {
      if (current.kind === "removePdf")
        await api.removePlacementDocument(token, current.drive.id);
      else if (current.kind === "generate")
        await api.placementAction(token, current.drive.id, "shortlist", {
          regenerate: current.regenerate,
        });
      else
        await api.placementAction(
          token,
          current.drive.id,
          current.kind,
          current.kind === "cancel" ? { reason } : {},
        );
      await refresh();
      setSuccess(
        current.kind === "generate"
          ? "Shortlist generated."
          : current.kind === "removePdf"
            ? "Job-description PDF removed."
            : "Drive status updated.",
      );
    });
  }
  return (
    <>
      <PageHeader title="Placement management" eyebrow="MAWOS / Careers">
        <p>Manage recruitment drives, shortlists and offers.</p>
      </PageHeader>
      <Notice error={error} success={success} />
      {busy && <p role="status">Working…</p>}
      <DashboardCard title={editing ? "Edit drive" : "Create drive"}>
        <form onSubmit={saveDrive}>
          <fieldset
            disabled={busy}
            className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
          >
            {[
              ["company", "Company", "text"],
              ["role", "Role", "text"],
              ["package_lpa", "Package (LPA)", "number"],
              ["drive_date", "Drive date", "date"],
              ["departments", "Departments", "text"],
              ["min_cgpa", "Minimum CGPA", "number"],
              ["max_backlogs", "Maximum backlogs", "number"],
              ["min_attendance", "Minimum attendance (%)", "number"],
              ["application_deadline", "Application deadline", "date"],
              ["application_url", "Official application link", "url"],
            ].map(([name, label, type]) => (
              <label key={name} className="text-sm">
                {label}
                <input
                  aria-label={label}
                  className="mt-1 w-full rounded border p-2"
                  type={type}
                  value={form[name]}
                  required={
                    !["application_deadline", "application_url"].includes(name)
                  }
                  onChange={(e) => setForm({ ...form, [name]: e.target.value })}
                />
              </label>
            ))}
            <label className="text-sm sm:col-span-2 lg:col-span-3">
              Job / company description
              <textarea
                className="mt-1 min-h-28 w-full rounded border p-2"
                value={form.description}
                onChange={(e) =>
                  setForm({ ...form, description: e.target.value })
                }
              />
            </label>
            {!editing && (
              <label className="text-sm">
                Initial status
                <select
                  className="mt-1 w-full rounded border p-2"
                  value={form.status}
                  onChange={(e) => setForm({ ...form, status: e.target.value })}
                >
                  <option>OPEN</option>
                  <option>DRAFT</option>
                </select>
              </label>
            )}
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={form.requires_fee_clearance}
                onChange={(e) =>
                  setForm({ ...form, requires_fee_clearance: e.target.checked })
                }
              />
              Require fee clearance
            </label>
            <label className="text-sm">
              PDF job description
              <input
                aria-label="PDF job description"
                className="mt-1 block"
                type="file"
                accept="application/pdf,.pdf"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
            </label>
            {editing && drives?.find((d) => d.id === editing)?.job_document && (
              <div className="text-sm">
                <p>
                  Current:{" "}
                  {
                    drives.find((d) => d.id === editing).job_document
                      .original_name
                  }{" "}
                  ·{" "}
                  {Math.ceil(
                    drives.find((d) => d.id === editing).job_document
                      .size_bytes / 1024,
                  )}{" "}
                  KB ·{" "}
                  {
                    drives.find((d) => d.id === editing).job_document
                      .uploaded_at
                  }
                </p>
                <button
                  type="button"
                  className="btn-secondary mt-1"
                  onClick={() =>
                    perform(() => viewPlacementDocument(token, editing))
                  }
                >
                  View current PDF
                </button>
                <button
                  type="button"
                  className="btn-secondary ml-2"
                  onClick={() =>
                    setModal({
                      kind: "removePdf",
                      drive: drives.find((d) => d.id === editing),
                      title: "Remove job-description PDF?",
                      message:
                        "The stored PDF will be removed. Other drive data is unchanged.",
                      confirmLabel: "Remove PDF",
                      destructive: true,
                    })
                  }
                >
                  Remove PDF
                </button>
              </div>
            )}
            <div className="flex items-end gap-2">
              <button className="btn-primary" type="submit">
                {editing ? "Save drive" : "Create drive"}
              </button>
              {editing && (
                <button className="btn-secondary" type="button" onClick={reset}>
                  Cancel edit
                </button>
              )}
            </div>
          </fieldset>
        </form>
      </DashboardCard>
      <div className="my-4">
        <DashboardCard title="Placement drives">
          {!drives ? (
            <p role="status">Loading drives…</p>
          ) : !drives.length ? (
            <p>No placement drives available.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr>
                    {[
                      "Company / role",
                      "Package / date",
                      "Departments",
                      "Status",
                      "Candidates / shortlisted",
                      "Actions",
                    ].map((x) => (
                      <th className="p-2" key={x}>
                        {x}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {drives.map((d) => (
                    <tr key={d.id} className="border-t">
                      <td className="p-2">
                        {d.company}
                        <br />
                        {d.role}
                      </td>
                      <td className="p-2">
                        ₹{d.package_lpa} LPA
                        <br />
                        {d.drive_date}
                      </td>
                      <td className="p-2">{d.departments}</td>
                      <td className="p-2">
                        <span className={badge}>{d.status}</span>
                        {d.cancellation_reason && (
                          <p>{d.cancellation_reason}</p>
                        )}
                      </td>
                      <td className="p-2">
                        {d.candidate_count ?? "—"} /{" "}
                        {d.shortlisted_count ?? "—"}
                      </td>
                      <td className="p-2">
                        <div className="flex flex-wrap gap-2">
                          {editable.includes(d.status) && (
                            <button
                              disabled={busy}
                              className="btn-secondary"
                              onClick={() => editDrive(d)}
                            >
                              Edit
                            </button>
                          )}
                          {active.includes(d.status) && (
                            <>
                              <button
                                disabled={busy}
                                className="btn-primary"
                                onClick={() => askGenerate(d)}
                              >
                                {d.status === "OPEN" && d.candidate_count === 0
                                  ? "Generate shortlist"
                                  : "Regenerate shortlist"}
                              </button>
                              <button
                                disabled={busy}
                                className="btn-secondary"
                                onClick={() => lifecycle(d, "close")}
                              >
                                Close
                              </button>
                            </>
                          )}
                          {!["CLOSED", "CANCELLED"].includes(d.status) && (
                            <button
                              disabled={busy}
                              className="btn-secondary"
                              onClick={() => lifecycle(d, "cancel")}
                            >
                              Cancel drive
                            </button>
                          )}
                          <Link
                            className="btn-secondary"
                            to={`/admin/placements/${d.id}`}
                          >
                            View shortlist / outcomes
                          </Link>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </DashboardCard>
      </div>
      <ConfirmModal
        modal={modal}
        busy={busy}
        onClose={() => setModal(null)}
        onConfirm={confirmModal}
      />
    </>
  );
}

export function AdminPlacementDetail() {
  const { token } = useAuth();
  const { driveId } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editingOutcome, setEditingOutcome] = useState(null);
  const load = async () =>
    setData(await api.placementAdminView(token, driveId));
  useEffect(() => {
    load().catch((err) => setError(err.message));
  }, [token, driveId]);
  async function saveOutcome(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.savePlacementOutcome(token, driveId, editingOutcome.usn, {
        outcome_status: editingOutcome.outcome_status,
        package_offered:
          editingOutcome.package_offered === ""
            ? null
            : Number(editingOutcome.package_offered),
        allow_multiple_offers: !!editingOutcome.allow_multiple_offers,
      });
      await load();
      setEditingOutcome(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  if (error && !data)
    return (
      <>
        <button
          className="btn-secondary"
          onClick={() => navigate("/admin/placements")}
        >
          Back to drive list
        </button>
        <div role="alert" className="mt-4">
          {error}
        </div>
      </>
    );
  if (!data) return <p role="status">Loading shortlist and outcomes…</p>;
  const d = data.drive;
  return (
    <>
      <button
        className="btn-secondary"
        onClick={() => navigate("/admin/placements")}
      >
        Back to drive list
      </button>
      <PageHeader
        title={`${d.company} · ${d.role}`}
        eyebrow="Shortlist and outcomes"
      >
        <p>
          ₹{d.package_lpa} LPA · {d.drive_date} ·{" "}
          <span className={badge}>{d.status}</span>
        </p>
      </PageHeader>
      <Notice error={error} />
      <DashboardCard title="Drive summary">
        <p>
          {d.departments} · CGPA ≥ {d.min_cgpa} · backlogs ≤ {d.max_backlogs} ·
          attendance ≥ {d.min_attendance}%
        </p>
        <p className="mt-2 whitespace-pre-wrap">
          {d.description || "No description provided."}
        </p>
      </DashboardCard>
      <div className="mt-4">
        <DashboardCard title="Shortlisted students">
          {!data.shortlist.length ? (
            <p>No candidates evaluated.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr>
                    <th>Student</th>
                    <th>Eligibility / evaluation / reasons</th>
                    <th>Outcome</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {data.shortlist.map((row) => (
                    <tr key={row.usn} className="border-t">
                      <td className="p-2">
                        {row.usn}
                        <br />
                        {row.name}
                      </td>
                      <td className="p-2">
                        <Evaluation entry={row} admin />
                      </td>
                      <td className="p-2">
                        {row.outcome ? (
                          <>
                            <span className={badge}>
                              {row.outcome.outcome_status}
                            </span>
                            <br />
                            {row.outcome.package_offered == null
                              ? "Package not recorded"
                              : `₹${row.outcome.package_offered} LPA`}
                          </>
                        ) : (
                          "Not recorded"
                        )}
                      </td>
                      <td>
                        <button
                          disabled={busy}
                          className="btn-secondary"
                          onClick={() =>
                            setEditingOutcome({
                              usn: row.usn,
                              outcome_status:
                                row.outcome?.outcome_status || "OFFER_MADE",
                              package_offered:
                                row.outcome?.package_offered ?? "",
                              allow_multiple_offers: false,
                            })
                          }
                        >
                          {row.outcome ? "Edit outcome" : "Record outcome"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </DashboardCard>
      </div>
      {!!data.other_outcomes.length && (
        <div className="mt-4">
          <DashboardCard title="Other recorded outcomes">
            <ul className="space-y-2">
              {data.other_outcomes.map((row) => (
                <li key={row.usn}>
                  {row.usn} · <span className={badge}>{row.outcome_status}</span>
                  {row.package_offered == null
                    ? " · Package not recorded"
                    : ` · ₹${row.package_offered} LPA`}
                </li>
              ))}
            </ul>
          </DashboardCard>
        </div>
      )}
      {editingOutcome && (
        <div className="mt-4">
          <DashboardCard title={`Outcome · ${editingOutcome.usn}`}>
            <form onSubmit={saveOutcome}>
              <fieldset disabled={busy} className="grid gap-3 sm:grid-cols-2">
                <label>
                  Outcome status
                  <select
                    aria-label="Outcome status"
                    className="block w-full rounded border p-2"
                    value={editingOutcome.outcome_status}
                    onChange={(e) =>
                      setEditingOutcome({
                        ...editingOutcome,
                        outcome_status: e.target.value,
                      })
                    }
                  >
                    {[
                      "OFFER_MADE",
                      "OFFER_ACCEPTED",
                      "OFFER_DECLINED",
                      "REJECTED",
                    ].map((x) => (
                      <option key={x}>{x}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Package offered (LPA)
                  <input
                    aria-label="Package offered (LPA)"
                    type="number"
                    min="0.01"
                    step="any"
                    className="block w-full rounded border p-2"
                    value={editingOutcome.package_offered}
                    onChange={(e) =>
                      setEditingOutcome({
                        ...editingOutcome,
                        package_offered: e.target.value,
                      })
                    }
                  />
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={editingOutcome.allow_multiple_offers}
                    onChange={(e) =>
                      setEditingOutcome({
                        ...editingOutcome,
                        allow_multiple_offers: e.target.checked,
                      })
                    }
                  />{" "}
                  Allow multiple accepted offers
                </label>
                <div>
                  <button className="btn-primary" type="submit">
                    Save outcome
                  </button>
                  <button
                    className="btn-secondary ml-2"
                    type="button"
                    onClick={() => setEditingOutcome(null)}
                  >
                    Cancel
                  </button>
                </div>
              </fieldset>
            </form>
          </DashboardCard>
        </div>
      )}
    </>
  );
}
