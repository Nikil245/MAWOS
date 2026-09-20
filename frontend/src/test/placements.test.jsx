import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import {
  AdminPlacementDetail,
  AdminPlacements,
  StudentPlacementDetail,
  StudentPlacements,
} from "../pages/placement/Placements";
import { RoleRoute } from "../components/routes";

const mocks = vi.hoisted(() =>
  Object.fromEntries(
    [
      "placementDrives",
      "placementDrive",
      "savePlacementDrive",
      "placementAction",
      "placementAdminView",
      "uploadPlacementDocument",
      "removePlacementDocument",
      "savePlacementOutcome",
    ].map((key) => [key, vi.fn()]),
  ),
);
const viewDocument = vi.hoisted(() => vi.fn());
let auth;
vi.mock("../context/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("../services/api", () => ({
  api: mocks,
  viewPlacementDocument: viewDocument,
}));

const drive = {
  id: 1,
  company: "Example",
  role: "Engineer",
  package_lpa: 8,
  drive_date: "2026-10-01",
  departments: "AIML",
  min_cgpa: 6,
  max_backlogs: 0,
  min_attendance: 75,
  status: "OPEN",
  requires_fee_clearance: false,
  application_deadline: "2099-10-01",
  description: "Line one\nLine two",
  application_url: "https://careers.example.com/job/1",
  candidate_count: 0,
  shortlisted_count: 0,
  job_document: null,
};
const evaluation = {
  eligible: true,
  status: "EVALUATED",
  ml_probability: null,
  model_version: null,
  reasons: "Meets hard rules",
  can_apply: true,
  apply_message: "Eligible to apply.",
};
const studentDrive = {
  ...drive,
  ...evaluation,
  status: drive.status,
  eligibility_status: evaluation.status,
};

function renderAt(ui, path = "/") {
  return render(<MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>);
}

beforeEach(() => {
  vi.clearAllMocks();
  auth = { token: "admin-token", user: { role: "admin" } };
  mocks.placementDrives.mockResolvedValue([drive]);
  mocks.savePlacementDrive.mockResolvedValue({ id: 2 });
  mocks.placementAction.mockResolvedValue({});
  mocks.uploadPlacementDocument.mockResolvedValue({});
  mocks.removePlacementDocument.mockResolvedValue({});
  mocks.savePlacementOutcome.mockResolvedValue({});
  mocks.placementAdminView.mockResolvedValue({
    drive,
    shortlist: [
      {
        usn: "P4",
        name: "Student",
        ...evaluation,
        ml_probability: 0.81,
        model_version: "rf-test",
        reasons: "First reason; Second reason",
        outcome: null,
      },
    ],
    other_outcomes: [],
  });
  mocks.placementDrive.mockResolvedValue({ usn: "P4", drive, ...evaluation });
});

describe("placement administration", () => {
  it("submits description/link and uploads a selected PDF", async () => {
    renderAt(<AdminPlacements />);
    await screen.findByRole("cell", { name: "Example Engineer" });
    for (const [label, value] of [
      ["Company", "New Co"],
      ["Role", "Developer"],
      ["Package (LPA)", "10"],
      ["Drive date", "2026-10-02"],
      ["Departments", "AIML"],
      ["Official application link", "https://jobs.example.com/1"],
    ])
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    fireEvent.change(screen.getByLabelText("Job / company description"), {
      target: { value: "Safe plain text" },
    });
    const pdf = new File(["%PDF-1.7"], "job.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("PDF job description"), {
      target: { files: [pdf] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create drive" }));
    await waitFor(() =>
      expect(mocks.savePlacementDrive).toHaveBeenCalledWith(
        "admin-token",
        null,
        expect.objectContaining({
          description: "Safe plain text",
          application_url: "https://jobs.example.com/1",
        }),
      ),
    );
    expect(mocks.uploadPlacementDocument).toHaveBeenCalledWith(
      "admin-token",
      2,
      pdf,
    );
  });

  it("shows date errors before submission and displays safe backend validation errors", async () => {
    renderAt(<AdminPlacements />);
    await screen.findByRole("cell", { name: "Example Engineer" });
    fireEvent.change(screen.getByLabelText("Drive date"), {
      target: { value: "2000-01-01" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create drive" }));
    expect(screen.getByText("Drive date cannot be in the past.")).toBeInTheDocument();
    expect(mocks.savePlacementDrive).not.toHaveBeenCalled();

    for (const [label, value] of [
      ["Company", "New Co"],
      ["Role", "Developer"],
      ["Package (LPA)", "10"],
      ["Drive date", "2099-10-02"],
      ["Application deadline", "2099-10-01"],
    ])
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    mocks.savePlacementDrive.mockRejectedValueOnce(
      new Error("Drive date must be on or after the application deadline."),
    );
    fireEvent.click(screen.getByRole("button", { name: "Create drive" }));
    await waitFor(() => expect(screen.getAllByText(
      "Drive date must be on or after the application deadline.",
    ).length).toBeGreaterThan(0));
  });

  it("hides initial status while editing and provides PDF controls", async () => {
    mocks.placementDrives.mockResolvedValue([
      {
        ...drive,
        job_document: {
          original_name: "role.pdf",
          size_bytes: 1200,
          uploaded_at: "2026-09-12T10:00:00",
        },
      },
    ]);
    renderAt(<AdminPlacements />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    expect(screen.queryByText("Initial status")).not.toBeInTheDocument();
    expect(screen.getByText(/Current: role.pdf/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove PDF" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove PDF" }));
    await waitFor(() =>
      expect(mocks.removePlacementDocument).toHaveBeenCalledWith(
        "admin-token",
        1,
      ),
    );
  });

  it("uses distinct generate, close, and required-reason cancel confirmations", async () => {
    renderAt(<AdminPlacements />);
    await screen.findByRole("cell", { name: "Example Engineer" });
    fireEvent.click(screen.getByRole("button", { name: "Generate shortlist" }));
    let dialog = screen.getByRole("dialog");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Generate shortlist" }),
    );
    await waitFor(() =>
      expect(mocks.placementAction).toHaveBeenCalledWith(
        "admin-token",
        1,
        "shortlist",
        { regenerate: false },
      ),
    );
    await screen.findByText("Shortlist generated.");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("finished normally");
    fireEvent.click(within(dialog).getByRole("button", { name: "Go back" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel drive" }));
    dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", {
      name: "Cancel drive",
    });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("Cancellation reason"), {
      target: { value: "Company withdrew" },
    });
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(mocks.placementAction).toHaveBeenCalledWith(
        "admin-token",
        1,
        "cancel",
        { reason: "Company withdrew" },
      ),
    );
  });

  it("navigates to a working shortlist/outcomes route with loading and details", async () => {
    renderAt(
      <Routes>
        <Route path="/admin/placements" element={<AdminPlacements />} />
        <Route
          path="/admin/placements/:driveId"
          element={<AdminPlacementDetail />}
        />
      </Routes>,
      "/admin/placements",
    );
    const link = await screen.findByRole("link", {
      name: "View shortlist / outcomes",
    });
    expect(link).toHaveAttribute("href", "/admin/placements/1");
    fireEvent.click(link);
    expect(
      await screen.findByText("Model evaluated · 0.81"),
    ).toBeInTheDocument();
    expect(screen.getByText("First reason")).toBeInTheDocument();
    expect(screen.getByText("Model version: rf-test")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Record outcome" }));
    fireEvent.click(screen.getByRole("button", { name: "Save outcome" }));
    await waitFor(() => expect(mocks.savePlacementOutcome).toHaveBeenCalled());
  });
});

describe("student placement details and authorization UI", () => {
  beforeEach(() => {
    auth = { token: "student-token", user: { role: "student", usn: "P4" } };
    mocks.placementDrives.mockResolvedValue([studentDrive]);
  });

  it("shows cards with a dedicated View details route and no admin controls", async () => {
    renderAt(<StudentPlacements />);
    const link = await screen.findByRole("link", { name: "View details" });
    expect(link).toHaveAttribute("href", "/student/placements/1");
    expect(screen.queryByText("Generate shortlist")).not.toBeInTheDocument();
  });

  it("shows full safe details and a protected external apply link", async () => {
    renderAt(
      <Routes>
        <Route
          path="/student/placements/:driveId"
          element={<StudentPlacementDetail />}
        />
      </Routes>,
      "/student/placements/1",
    );
    expect(
      await screen.findByText("Line one", { exact: false }),
    ).toBeInTheDocument();
    const apply = screen.getByRole("link", { name: "Apply on company portal" });
    expect(apply).toHaveAttribute("href", drive.application_url);
    expect(apply).toHaveAttribute("target", "_blank");
    expect(apply).toHaveAttribute("rel", "noopener noreferrer");
  });

  it.each([
    [
      "not eligible",
      {
        eligible: false,
        can_apply: false,
        apply_message: "You are not currently eligible for this drive.",
      },
    ],
    [
      "expired",
      {
        can_apply: false,
        apply_message: "The application deadline has passed.",
      },
    ],
    [
      "closed",
      {
        can_apply: false,
        apply_message: "This drive is closed.",
        drive: { ...drive, status: "CLOSED" },
      },
    ],
    [
      "no link",
      {
        can_apply: false,
        apply_message:
          "The company has not provided an external application link.",
        drive: { ...drive, application_url: null },
      },
    ],
  ])("explains disabled apply state: %s", async (_label, changes) => {
    mocks.placementDrive.mockResolvedValue({
      usn: "P4",
      drive,
      ...evaluation,
      ...changes,
    });
    renderAt(
      <Routes>
        <Route
          path="/student/placements/:driveId"
          element={<StudentPlacementDetail />}
        />
      </Routes>,
      "/student/placements/1",
    );
    expect(await screen.findByText(changes.apply_message)).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Apply on company portal" }),
    ).not.toBeInTheDocument();
  });

  it("blocks student access to admin detail routes", async () => {
    renderAt(
      <Routes>
        <Route element={<RoleRoute roles={["admin"]} />}>
          <Route
            path="/admin/placements/:driveId"
            element={<AdminPlacementDetail />}
          />
        </Route>
        <Route path="/student" element={<p>Student home</p>} />
      </Routes>,
      "/admin/placements/1",
    );
    expect(await screen.findByText("Student home")).toBeInTheDocument();
    expect(mocks.placementAdminView).not.toHaveBeenCalled();
  });
});
