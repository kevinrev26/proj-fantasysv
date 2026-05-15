// frontend/static/js/auth.js
async function getToken() {
  return localStorage.getItem('jwt_token') || null;
}

async function authFetch(url, options = {}) {
  const token = getToken();
  
  if (token) {
    options.headers = {
      ...options.headers,
      'Authorization': `Bearer ${token}`
    };
  }
  
  return fetch(url, options)
    .then(response => {
      if (response.status === 401) {
        // Clear token and redirect to login
        localStorage.removeItem('jwt_token');
        window.location.href = '/login';
        return Promise.reject(new Error('Unauthorized'));
      }
      return response;
    });
}

async function requireRole(requiredRole) {
  const token = getToken();
  if (!token) {
    window.location.href = '/login';
    return false;
  }
  
  try {
    // Decode JWT payload (second part)
    const payload = token.split('.')[1];
    const decoded = atob(payload);
    const claims = JSON.parse(decoded);
    
    if (claims.role !== requiredRole) {
      window.location.href = '/login';
      return false;
    }
    
    return true;
  } catch (error) {
    window.location.href = '/login';
    return false;
  }
}
