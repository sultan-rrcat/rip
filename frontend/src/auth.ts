/** Shared auth shape: the logged-in user with their access role (ADR-052). */

export interface AuthUser {
  username: string
  role: string
}

export function isAdminRole(role: string | undefined): boolean {
  // Fail-closed: only the exact 'admin' value grants the panel.
  return role === 'admin'
}
