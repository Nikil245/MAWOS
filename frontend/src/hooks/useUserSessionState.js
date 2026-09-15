import { useCallback, useEffect, useMemo, useState } from 'react';

export const USER_SESSION_PREFIX = 'mawos_ui:v1:';
const DEFAULT_TTL_MS = 12 * 60 * 60 * 1000;

export function userSessionIdentity(user) {
  const identifier = user?.id ?? user?.usn ?? user?.username;
  if (identifier === undefined || identifier === null || !String(identifier).trim() || !user?.role) return null;
  return { identifier: String(identifier), role: String(user.role) };
}

export function userSessionKey(user, namespace) {
  const owner = userSessionIdentity(user);
  if (!owner || !namespace) return null;
  return `${USER_SESSION_PREFIX}${encodeURIComponent(owner.role)}:${encodeURIComponent(owner.identifier)}:${namespace}`;
}

function initialValue(value) {
  return typeof value === 'function' ? value() : value;
}

function removeSessionKey(key) {
  try {
    sessionStorage.removeItem(key);
  } catch {
    // Storage may be unavailable; callers still reset their in-memory state.
  }
}

export function readUserSessionState(user, namespace, fallback, validate = () => true) {
  const key = userSessionKey(user, namespace);
  if (!key) return initialValue(fallback);
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return initialValue(fallback);
    const parsed = JSON.parse(raw);
    const owner = userSessionIdentity(user);
    if (parsed?.version !== 1 || parsed.owner?.identifier !== owner.identifier
      || parsed.owner?.role !== owner.role || !Number.isFinite(parsed.expiresAt)
      || parsed.expiresAt <= Date.now() || !validate(parsed.value)) {
      removeSessionKey(key);
      return initialValue(fallback);
    }
    return parsed.value;
  } catch {
    removeSessionKey(key);
    return initialValue(fallback);
  }
}

export function writeUserSessionState(user, namespace, value, ttlMs = DEFAULT_TTL_MS) {
  const key = userSessionKey(user, namespace);
  const owner = userSessionIdentity(user);
  if (!key || !owner) return;
  try {
    sessionStorage.setItem(key, JSON.stringify({
      version: 1,
      owner,
      expiresAt: Date.now() + Math.min(Math.max(ttlMs, 1), DEFAULT_TTL_MS),
      value,
    }));
  } catch {
    // Storage may be unavailable or full. In-memory UI state still works.
  }
}

export function clearAllUserSessionState() {
  try {
    for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
      const key = sessionStorage.key(index);
      if (key?.startsWith(USER_SESSION_PREFIX)) sessionStorage.removeItem(key);
    }
  } catch {
    // Logout must continue even when browser storage is unavailable.
  }
}

export function useUserSessionState(user, namespace, fallback, options = {}) {
  const key = userSessionKey(user, namespace);
  const validate = options.validate || (() => true);
  const [snapshot, setSnapshot] = useState(() => ({
    key,
    value: readUserSessionState(user, namespace, fallback, validate),
  }));
  const current = snapshot.key === key
    ? snapshot.value
    : readUserSessionState(user, namespace, fallback, validate);

  useEffect(() => {
    if (snapshot.key !== key) {
      setSnapshot({ key, value: readUserSessionState(user, namespace, fallback, validate) });
    }
  }, [key]);

  useEffect(() => {
    if (!key || snapshot.key !== key) return;
    const stored = options.serialize ? options.serialize(snapshot.value) : snapshot.value;
    if (stored === null || stored === undefined) {
      removeSessionKey(key);
      return;
    }
    writeUserSessionState(user, namespace, stored, options.ttlMs);
  }, [key, snapshot]);

  const setValue = useCallback((next) => {
    setSnapshot((previous) => {
      const base = previous.key === key
        ? previous.value
        : readUserSessionState(user, namespace, fallback, validate);
      return { key, value: typeof next === 'function' ? next(base) : next };
    });
  }, [key, namespace, user]);

  const clear = useCallback(() => {
    if (key) removeSessionKey(key);
    setSnapshot({ key, value: initialValue(fallback) });
  }, [key, fallback]);

  return useMemo(() => [current, setValue, clear], [current, setValue, clear]);
}
