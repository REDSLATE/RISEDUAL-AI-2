/**
 * Compute the API base URL dynamically at runtime.
 * Handles deployed environments where REACT_APP_BACKEND_URL (baked at build time)
 * may point to the preview domain instead of the deployed domain.
 *
 * Logic:
 *  - If the env URL matches the current page origin → use it (preview / local dev).
 *  - If they differ → the app was deployed to a new domain; use the current origin
 *    so cookies and CORS work correctly.
 */
export const getApiBase = () => {
  const envUrl = process.env.REACT_APP_BACKEND_URL;
  if (typeof window !== 'undefined' && envUrl && window.location.origin !== envUrl) {
    return window.location.origin;
  }
  return envUrl || '';
};
