export const defaultRouteByRole = {
  student: '/student',
  faculty: '/faculty',
  hod: '/hod',
  principal: '/principal',
  admin: '/admin',
  parent: '/parent',
  librarian: '/librarian/library',
};

export const scholarshipRouteByRole = {
  student: '/student/scholarships',
  faculty: '/faculty/scholarships',
  hod: '/hod/scholarships',
};

const allowedPaths = {
  student: ['/student/library', '/events', '/student/placements', '/student', '/student/timetable', '/student/scholarships', '/assistant', '/system'],
  faculty: ['/events', '/faculty', '/faculty/timetable', '/faculty/coverage', '/faculty/scholarships', '/assistant', '/system'],
  hod: ['/events', '/hod/timetable', '/faculty/timetable', '/hod/coverage', '/faculty/coverage', '/hod', '/hod/scholarships', '/faculty', '/assistant', '/system'],
  principal: ['/events', '/principal/timetable', '/coverage/escalations', '/principal', '/assistant', '/system'],
  admin: ['/admin/library', '/admin/librarians', '/admin/parents', '/events', '/admin/events', '/admin/placements', '/admin/timetable', '/principal/timetable', '/coverage/escalations', '/admin', '/principal', '/assistant', '/system'],
  librarian: ['/librarian/library'],
  parent: ['/parent', '/parent/timetable', '/parent/notifications'],
};

export function isRouteAllowedForRole(role, pathname) {
  return typeof pathname === 'string' && (allowedPaths[role]?.includes(pathname)
    || (/^\/events\/\d+$/.test(pathname) && allowedPaths[role]?.includes('/events'))
    || (role === 'student' && /^\/student\/library\/slips\/\d+$/.test(pathname))
    || (role === 'parent' && /^\/parent\/events\/\d+$/.test(pathname)));
}

// A staff scholarship URL is always redirected to the authenticated role's
// scholarship workspace. Other protected mismatches go to that role's normal landing page.
export function roleMismatchDestination(role, pathname) {
  if (pathname === '/faculty/scholarships' || pathname === '/hod/scholarships') {
    return scholarshipRouteByRole[role] || defaultRouteByRole[role] || '/login';
  }
  return defaultRouteByRole[role] || '/login';
}

export function safeReturnPath(role, from) {
  const pathname = from?.pathname;
  if (!isRouteAllowedForRole(role, pathname)) return null;
  const search = typeof from.search === 'string' && from.search.startsWith('?') ? from.search : '';
  const hash = typeof from.hash === 'string' && from.hash.startsWith('#') ? from.hash : '';
  return `${pathname}${search}${hash}`;
}
