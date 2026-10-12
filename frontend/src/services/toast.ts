/** Minimal user-facing toast bus (Phase D).
 *
 * Hooks dispatch via `notifyToast(message)` — no prop drilling, no new
 * dependency. `ToastHost` (mounted once per page) renders them.
 * `console.error` stays at call sites as the debug log; the toast is the
 * user surface.
 */

export const TOAST_EVENT = 'rip:toast'

export function notifyToast(message: string): void {
  window.dispatchEvent(new CustomEvent<string>(TOAST_EVENT, { detail: message }))
}
