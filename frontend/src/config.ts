const raw: string = import.meta.env.VITE_API_URL ?? ""

// Empty/undefined = same-origin (prod nginx proxies /api/ + /v1/ to the
// backend). Strip trailing slashes so `${API}/api/...` never doubles them.
export const API: string = raw.trim().replace(/\/+$/, "")

if (!API) {
  console.warn("[config] VITE_API_URL empty — using same-origin relative URLs")
}
