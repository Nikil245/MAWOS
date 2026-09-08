const API_ROOT = '/api';
let onUnauthorized = null;
export function setUnauthorizedHandler(handler) { onUnauthorized = handler; }

export class ApiError extends Error {
  constructor(message, status, category = 'request') {
    super(message); this.status = status; this.category = category;
  }
}

export function isAbortError(error) {
  return error?.name === 'AbortError';
}

function errorCategory(status) {
  if (status === 401) return 'authentication';
  if (status === 403) return 'authorization';
  if (status >= 500) return 'server';
  return 'request';
}

export async function request(path, { method, body, token, signal } = {}) {
  let response;
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      method: method || (body ? 'POST' : 'GET'), signal,
      headers: {
        ...(body ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new ApiError('Network request failed', 0, 'network');
  }
  if (!response.ok) {
    let message = response.statusText;
    try { message = (await response.json()).detail || message; } catch { /* non JSON error */ }
    const error = new ApiError(message, response.status, errorCategory(response.status));
    if (error.status === 401) onUnauthorized?.();
    throw error;
  }
  const type = response.headers.get('content-type') || '';
  return type.includes('application/json') ? response.json() : response;
}

export const api = {
  login: (username, password) => request('/auth/login', { body: { username, password } }),
  me: (token) => request('/me', { token }),
  notifications: (token, signal) => request('/notifications', { token, signal }),
  markNotificationRead: (token, id) => request(`/notifications/${id}/read`, { token, method: 'PATCH' }),
  markAllNotificationsRead: (token) => request('/notifications/read-all', { token, method: 'POST' }),
  studentDashboard: (token) => request('/student/dashboard', { token }),
  payFee: (token, fee_id) => request('/student/pay-fee', { token, body: { fee_id } }),
  facultyOverview: (token) => request('/faculty/overview', { token }),
  roster: (token, dept, year, section) => request(`/faculty/roster/${dept}/${year}/${section}`, { token }),
  attendance: (token, body) => request('/faculty/attendance', { token, body }),
  marksPolicy: (token) => request('/faculty/marks-policy', { token }),
  marks: (token, body) => request('/faculty/marks', { token, body }),
  scholarships: (token, role) => request(`/${role}/scholarships`, { token }),
  scholarship: (token, role, id) => request(`/${role}/scholarships/${id}`, { token }),
  scholarshipAction: (token, path, body) => request(path, { token, body, method: 'POST' }),
  saveScholarship: (token, id, body) => request(id ? `/faculty/scholarships/${id}` : '/faculty/scholarships', { token, body, method: id ? 'PUT' : 'POST' }),
  hodAnalytics: (token) => request('/hod/analytics', { token }),
  generateTimetable: (token) => request('/hod/generate-timetable', { token, method: 'POST' }),
  timetable: (token, dept, year, section) => request(`/timetable/${dept}/${year}/${section}`, { token }),
  principalAnalytics: (token) => request('/principal/analytics', { token }),
  admissions: (token) => request('/admin/admissions', { token }),
  adminAction: (token, path, body) => request(path, { token, body, method: 'POST' }),
  assistantCapabilities: (token, signal) => request('/assistant/capabilities', { token, signal }),
  chat: (token, message, signal, contextTopic = null, generalContext = []) => request('/chat', {
    token, body: { message, context_topic: contextTopic, general_context: generalContext }, signal,
  }),
  metrics: (token) => request('/metrics/summary', { token }),
  agents: (token) => request('/agents', { token }),
  workflows: (token) => request('/workflows/recent', { token }),
  workflow: (token, id) => request(`/workflows/${id}`, { token }),
};

export async function downloadTimetable(token, dept, year, section) {
  const response = await request(`/timetable/${dept}/${year}/${section}/csv`, { token });
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = `timetable_${dept}_${year}${section}.csv`; anchor.click();
  URL.revokeObjectURL(url);
}
