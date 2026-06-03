// frontend/static/js/auth.js

function getToken() {
  return localStorage.getItem("jwt_token") || null;
}

async function authFetch(url, options = {}) {
  const token = getToken();

  if (token) {
    options.headers = {
      ...options.headers,
      Authorization: `Bearer ${token}`,
    };
  }

  return fetch(url, options).then((response) => {
    if (response.status === 401) {
      // Token rejected by server — clear and redirect
      localStorage.removeItem("jwt_token");
      window.location.href = "/login";
      return Promise.reject(new Error("Unauthorized"));
    }
    return response;
  });
}

async function requireRole(requiredRole) {
  const token = getToken();
  if (!token) {
    window.location.href = "/login";
    return false;
  }

  try {
    const payload = token.split(".")[1];
    // Fix base64url → base64 padding before decoding
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64.padEnd(
      base64.length + ((4 - (base64.length % 4)) % 4),
      "=",
    );
    const claims = JSON.parse(atob(padded));

    if (claims.role !== requiredRole) {
      window.location.href = "/unauthorized";
      return false;
    }

    return true;
  } catch (error) {
    console.error("requireRole error:", error);
    window.location.href = "/login";
    return false;
  }
}

/**
 * Log the current user out.
 * - Calls POST /auth/logout so the server blocklists the token in Redis.
 * - Clears localStorage regardless of server response.
 * - Redirects to /login.
 */
async function logout() {
  const token = getToken();

  if (token) {
    try {
      await fetch("/auth/logout", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
    } catch (e) {
      // Network error — still proceed with client-side logout
      console.warn("Logout request failed:", e);
    }
  }

  localStorage.removeItem("jwt_token");
  localStorage.removeItem("user_id");
  // if (window.umami) {
  //   window.umami.track("logout");
  // }

  window.location.href = "/login";
}
