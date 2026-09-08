export const defaultRouteByRole = {
  student: '/student',
  faculty: '/faculty',
  hod: '/hod',
  principal: '/principal',
  admin: '/admin',
};

export const scholarshipRouteByRole = {
  student: '/student/scholarships',
  faculty: '/faculty/scholarships',
  hod: '/hod/scholarships',
};

const allowedPaths = {
  student: ['/student', '/student/timetable', '/student/scholarships', '/assistant', '/system'],
  faculty: ['/faculty', '/faculty/timetable', '/faculty/scholarships', '/assistant', '/system'],
  hod: ['/hod', '/hod/scholarships', '/faculty', '/assistant', '/system'],
  principal: ['/principal', '/assistant', '/system'],
  admin: ['/admin', '/principal', '/assistant', '/system'],
};

export function isRouteAllowedForRole(role, pathname) {
  return typeof pathname === 'string' && allowedPaths[role]?.includes(pathname);
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
