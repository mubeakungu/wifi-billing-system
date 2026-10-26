/**
 * Shared API helpers
 */

// Set REACT_APP_API_URL when the API lives elsewhere. Otherwise: the dev server
// (port 3000) talks to a local backend, and a build served by the backend uses /api.
export const API_BASE_URL = process.env.REACT_APP_API_URL ||
  (window.location.port === '3000' ? 'http://localhost:5000/api' : '/api');

export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

export const apiCall = async (endpoint, { token, ...options } = {}) => {
  const headers = { 'Content-Type': 'application/json', ...options.headers };
  if (token) headers.Authorization = `Bearer ${token}`;

  let response;
  try {
    response = await fetch(`${API_BASE_URL}${endpoint}`, { ...options, headers });
  } catch (e) {
    throw new ApiError('Cannot reach the server. Check your connection.', 0);
  }

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(data.error || `Request failed (${response.status})`, response.status, data);
  }
  return data;
};
