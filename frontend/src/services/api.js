// Default to a same-origin relative path so neither development nor the
// production bundle hard-codes a localhost or Docker-only address.
const configuredApiRoot = import.meta.env.VITE_API_BASE_URL?.trim();
const API_ROOT = (configuredApiRoot || "/api").replace(/\/$/, "");
let onUnauthorized = null;
export function setUnauthorizedHandler(handler) {
  onUnauthorized = handler;
}

export class ApiError extends Error {
  constructor(message, status, category = "request") {
    super(message);
    this.status = status;
    this.category = category;
  }
}

export function isAbortError(error) {
  return error?.name === "AbortError";
}

const CHAT_TOPICS = new Set([
  "attendance", "fees", "marks", "eligibility", "greeting", "thanks", "identity",
  "help", "attendance_meaning", "attendance_requirement", "cie", "eligibility_meaning",
  "fees_meaning", "improve_attendance", "mawos", "reason_codes", "profile", "rag",
]);
const CONTEXT_CATEGORIES = new Set(["general_ai", "library_catalogue"]);
const CONTEXT_PROOF = /^[0-9a-f]{64}$/;
const ISBN = /^[0-9Xx -]{1,32}$/;

function safeContextBooks(value) {
  if (!Array.isArray(value) || value.length > 5) return null;
  const books = [];
  for (const book of value) {
    if (!book || typeof book !== "object"
      || typeof book.title !== "string" || !book.title || book.title.length > 240
      || typeof book.isbn !== "string" || !ISBN.test(book.isbn)
      || typeof book.author !== "string" || !book.author || book.author.length > 240
      || typeof book.category !== "string" || !book.category || book.category.length > 120) return null;
    books.push({ title: book.title, isbn: book.isbn, author: book.author, category: book.category });
  }
  return books;
}

function safeConversationContext(value) {
  try {
    if (!Array.isArray(value) || value.length === 0 || value.length > 8 || value.length % 2) return [];
    const result = [];
    let characters = 0;
    for (let index = 0; index < value.length; index += 2) {
      const priorUser = value[index];
      const priorAssistant = value[index + 1];
      if (!priorUser || !priorAssistant || priorUser.role !== "user"
        || priorAssistant.role !== "assistant"
        || !CONTEXT_CATEGORIES.has(priorUser.category)
        || priorAssistant.category !== priorUser.category
        || typeof priorUser.content !== "string" || !priorUser.content.trim()
        || typeof priorAssistant.content !== "string" || !priorAssistant.content.trim()
        || priorUser.content.length > 700 || priorAssistant.content.length > 700
        || !CONTEXT_PROOF.test(priorAssistant.proof || "")) return [];
      const userMessage = {
        role: "user", category: priorUser.category, content: priorUser.content,
      };
      const assistantMessage = {
        role: "assistant", category: priorAssistant.category,
        content: priorAssistant.content, proof: priorAssistant.proof,
      };
      if (priorAssistant.category === "library_catalogue") {
        const books = safeContextBooks(priorAssistant.books);
        if (books === null) return [];
        assistantMessage.books = books;
        if (books.length) {
          if (!Number.isSafeInteger(priorAssistant.issued_at) || priorAssistant.issued_at < 0) return [];
          assistantMessage.issued_at = priorAssistant.issued_at;
        }
      }
      characters += userMessage.content.length + assistantMessage.content.length;
      if (characters > 4000) return [];
      result.push(userMessage, assistantMessage);
    }
    // This assertion also catches exotic values/getters before request() reaches fetch.
    JSON.stringify(result);
    return result;
  } catch {
    return [];
  }
}

export function buildChatPayload(message, contextTopic, conversationContext) {
  const payload = { message };
  try {
    if (typeof contextTopic === "string" && CHAT_TOPICS.has(contextTopic)) {
      payload.context_topic = contextTopic;
    }
    const safeContext = safeConversationContext(conversationContext);
    if (safeContext.length) payload.conversation_context = safeContext;
  } catch {
    // Optional conversational state must never prevent sending the current message.
  }
  return payload;
}

function errorCategory(status) {
  if (status === 401) return "authentication";
  if (status === 403) return "authorization";
  if (status >= 500) return "server";
  return "request";
}

export async function request(
  path,
  { method, body, rawBody, token, signal } = {},
) {
  let response;
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      method: method || (body || rawBody ? "POST" : "GET"),
      signal,
      headers: {
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body ? JSON.stringify(body) : rawBody,
    });
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new ApiError("Network request failed", 0, "network");
  }
  if (!response.ok) {
    let message = response.statusText;
    try {
      message = (await response.json()).detail || message;
    } catch {
      /* non JSON error */
    }
    if (typeof message === "object")
      message = message.message
        ? `${message.message} ${(message.issues || []).map((i) => i.message).join(" ")}`
        : "Request validation failed. Check the supplied values.";
    const error = new ApiError(
      message,
      response.status,
      errorCategory(response.status),
    );
    if (error.status === 401) onUnauthorized?.();
    throw error;
  }
  const type = response.headers.get("content-type") || "";
  return type.includes("application/json") ? response.json() : response;
}

export const api = {
  libraryRequest: (token, path, body, method) => request(path, { token, body, method }),
  downloadLibrarySlip: async (token, id) => {
    const response = await request(`/student/library/reservations/${encodeURIComponent(id)}/slip.pdf`, { token });
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `library-slip-${id}.pdf`;
    document.body.appendChild(anchor); anchor.click(); anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
  campusEvents: (token) => request("/campus-events", { token }),
  campusEvent: (token, id) => request(`/campus-events/${id}`, { token }),
  adminCampusEvents: (token) => request("/admin/campus-events", { token }),
  saveCampusEvent: (token, id, body) =>
    request(id ? `/admin/campus-events/${id}` : "/admin/campus-events", {
      token,
      body,
      method: id ? "PUT" : "POST",
    }),
  campusEventAction: (token, id, action, body = {}) =>
    request(`/admin/campus-events/${id}/${action}`, {
      token,
      body,
      method: "POST",
    }),
  placementDrives: (token) => request("/placements/drives", { token }),
  placementDrive: (token, id) => request(`/placements/drives/${id}`, { token }),
  savePlacementDrive: (token, id, body) =>
    request(id ? `/placements/drives/${id}` : "/placements/drives", {
      token,
      body,
      method: id ? "PUT" : "POST",
    }),
  placementAction: (token, id, action, body = {}) =>
    request(`/placements/drives/${id}/${action}`, {
      token,
      body,
      method: "POST",
    }),
  placementShortlist: (token, id) =>
    request(`/placements/drives/${id}/shortlist`, { token }),
  placementEligibility: (token, id, usn) =>
    request(`/placements/drives/${id}/eligibility/${encodeURIComponent(usn)}`, {
      token,
    }),
  placementOutcomes: (token, id) =>
    request(`/placements/drives/${id}/outcomes`, { token }),
  placementAdminView: (token, id) =>
    request(`/placements/drives/${id}/admin-view`, { token }),
  uploadPlacementDocument: (token, id, file) => {
    const data = new FormData();
    data.append("document", file);
    return request(`/placements/drives/${id}/document`, {
      token,
      method: "POST",
      rawBody: data,
    });
  },
  removePlacementDocument: (token, id) =>
    request(`/placements/drives/${id}/document`, { token, method: "DELETE" }),
  savePlacementOutcome: (token, id, usn, body) =>
    request(`/placements/drives/${id}/outcomes/${encodeURIComponent(usn)}`, {
      token,
      body,
      method: "PUT",
    }),
  login: (username, password) =>
    request("/auth/login", { body: { username, password } }),
  changePassword: (token, body) => request("/auth/change-password", { token, body }),
  me: (token) => request("/me", { token }),
  parentProfile: (token) => request("/parent/profile", { token }),
  parentDashboard: (token, usn) => request(`/parent/children/${encodeURIComponent(usn)}/dashboard`, { token }),
  parentTimetable: (token, usn) => request(`/parent/children/${encodeURIComponent(usn)}/timetable`, { token }),
  adminParents: (token, q = "") => request(`/admin/parents?q=${encodeURIComponent(q)}`, { token }),
  searchStudents: (token, q) => request(`/admin/students/search?q=${encodeURIComponent(q)}`, { token }),
  createParent: (token, body) => request("/admin/parents", { token, body }),
  updateParent: (token, id, body) => request(`/admin/parents/${id}`, { token, body, method: "PUT" }),
  setParentActive: (token, id, active) => request(`/admin/parents/${id}/active`, { token, body: { active }, method: "PATCH" }),
  addParentStudent: (token, id, body) => request(`/admin/parents/${id}/students`, { token, body }),
  setParentStudentActive: (token, parentId, mappingId, active) => request(`/admin/parents/${parentId}/students/${mappingId}/active`, { token, body: { active }, method: "PATCH" }),
  notifications: (token, signal) =>
    request("/notifications", { token, signal }),
  markNotificationRead: (token, id) =>
    request(`/notifications/${id}/read`, { token, method: "PATCH" }),
  markAllNotificationsRead: (token) =>
    request("/notifications/read-all", { token, method: "POST" }),
  studentDashboard: (token) => request("/student/dashboard", { token }),
  payFee: (token, fee_id) =>
    request("/student/pay-fee", { token, body: { fee_id } }),
  facultyOverview: (token) => request("/faculty/overview", { token }),
  roster: (token, dept, year, section) =>
    request(`/faculty/roster/${dept}/${year}/${section}`, { token }),
  attendance: (token, body) => request("/faculty/attendance", { token, body }),
  attendanceOccurrences: (token) => request("/coverage/faculty/attendance-occurrences", { token }),
  attendanceOccurrenceRoster: (token, id) => request(`/coverage/faculty/attendance-occurrences/${id}/roster`, { token }),
  facultyAbsences: (token) => request("/coverage/faculty/absences", { token }),
  createFacultyAbsence: (token, body) => request("/coverage/faculty/absences", { token, body }),
  facultyAbsenceAction: (token, id, action) => request(`/coverage/faculty/absences/${id}/${action}`, { token, body: {} }),
  facultyCoverageAssignments: (token) => request("/coverage/faculty/assignments", { token }),
  facultyCoverageResponse: (token, id, action) => request(`/coverage/faculty/assignments/${id}/${action}`, { token, body: {} }),
  coverageAbsenceQueue: (token, escalated = false) => request(`/coverage/${escalated ? "escalations" : "hod"}/absence-queue`, { token }),
  coverageRequestQueue: (token, escalated = false) => request(`/coverage/${escalated ? "escalations" : "hod"}/requests`, { token }),
  reviewFacultyAbsence: (token, id, decision) => request(`/coverage/absences/${id}/review`, { token, body: { decision } }),
  coverageCandidates: (token, id) => request(`/coverage/requests/${id}/candidates`, { token }),
  approveCoverageCandidate: (token, id, substituteFacultyId) => request(`/coverage/requests/${id}/approve`, { token, body: { substitute_faculty_id: substituteFacultyId } }),
  markCoverageUnfilled: (token, id) => request(`/coverage/requests/${id}/unfilled`, { token, body: {} }),
  declineCoverageRequest: (token, id) => request(`/coverage/requests/${id}/decline`, { token, body: {} }),
  marksPolicy: (token) => request("/faculty/marks-policy", { token }),
  marks: (token, body) => request("/faculty/marks", { token, body }),
  scholarships: (token, role) => request(`/${role}/scholarships`, { token }),
  scholarship: (token, role, id) =>
    request(`/${role}/scholarships/${id}`, { token }),
  scholarshipAction: (token, path, body) =>
    request(path, { token, body, method: "POST" }),
  saveScholarship: (token, id, body) =>
    request(id ? `/faculty/scholarships/${id}` : "/faculty/scholarships", {
      token,
      body,
      method: id ? "PUT" : "POST",
    }),
  hodAnalytics: (token) => request("/hod/analytics", { token }),
  timetable: (token, dept, year, section) =>
    request(`/timetable/${dept}/${year}/${section}`, { token }),
  principalAnalytics: (token) => request("/principal/analytics", { token }),
  admissions: (token) => request("/admin/admissions", { token }),
  adminAction: (token, path, body) =>
    request(path, { token, body, method: "POST" }),
  assistantCapabilities: (token, signal) =>
    request("/assistant/capabilities", { token, signal }),
  chat: (token, message, signal, contextTopic = null, conversationContext = []) =>
    request("/chat", {
      token,
      body: buildChatPayload(message, contextTopic, conversationContext),
      signal,
    }),
  metrics: (token) => request("/metrics/summary", { token }),
  agents: (token) => request("/agents", { token }),
  workflows: (token) => request("/workflows/recent", { token }),
  workflow: (token, id) => request(`/workflows/${id}`, { token }),
};

export async function viewPlacementDocument(token, id, disposition = "inline") {
  const response = await request(
    `/placements/drives/${id}/document?disposition=${disposition}`,
    { token },
  );
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank", "noopener,noreferrer");
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export async function downloadTimetable(token, dept, year, section) {
  const response = await request(`/timetable/${dept}/${year}/${section}/csv`, {
    token,
  });
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `timetable_${dept}_${year}${section}.csv`;
  anchor.click();
  URL.revokeObjectURL(url);
}
