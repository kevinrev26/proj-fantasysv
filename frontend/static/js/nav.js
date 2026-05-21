// ─────────────────────────────────────────────────────────────
// Renders only the auth slot (#nav-auth) inside whatever nav
// structure the page already has. Does NOT touch the surrounding
// markup, mobile toggles, or existing links.
//
// USAGE — add to every page that needs it:
//
//   1. In your nav, where the auth buttons live:
//        <span id="nav-auth"></span>
//
//   2. After your <script src="/js/auth.js"> tag:
//        <script src="/js/nav.js"></script>
//
//   3. At the bottom of the page (or inside your init/DOMContentLoaded):
//        setupNavAuth();
//
// ─────────────────────────────────────────────────────────────

function setupNavAuth() {
  const slot = document.getElementById('nav-auth');
  if (!slot) return;

  const token = getToken();

  if (token) {
    slot.innerHTML = `
      <button
        class="nav-auth-btn nav-auth-logout"
        onclick="logout()"
        title="Cerrar Sesión"
      >Cerrar Sesión</button>
    `;
  } else {
    slot.innerHTML = `
      <a href="/login"    class="nav-auth-btn nav-auth-login">Iniciar Sesión</a>
      <a href="/register" class="nav-auth-btn nav-auth-register">Registrarse</a>
    `;
  }
}

// ─── Styles injected once so they work regardless of which
//     page's CSS is loaded. Uses !important only where needed
//     to survive different nav themes. ───────────────────────
(function injectNavAuthStyles() {
  if (document.getElementById('nav-auth-styles')) return;
  const style = document.createElement('style');
  style.id = 'nav-auth-styles';
  style.textContent = `
    #nav-auth {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      flex-shrink: 0;
    }

    .nav-auth-btn {
      display: inline-flex;
      align-items: center;
      font-family: inherit;
      font-size: .82rem;
      font-weight: 700;
      padding: 6px 14px;
      border-radius: 50px;
      border: none;
      cursor: pointer;
      text-decoration: none;
      white-space: nowrap;
      transition: background .15s, color .15s, opacity .15s;
      line-height: 1.4;
    }

    /* ── Logged-in: logout button ── */
    .nav-auth-logout {
      background: transparent;
      color: rgba(232,240,255,.45);
    }
    .nav-auth-logout:hover {
      background: rgba(255, 77, 109, .1);
      color: #ff4d6d;
    }

    /* ── Logged-out: login link ── */
    .nav-auth-login {
      background: transparent;
      color: rgba(232,240,255,.55);
    }
    .nav-auth-login:hover {
      background: rgba(255,255,255,.07);
      color: #e8f0ff;
    }

    /* ── Logged-out: register link (accented) ── */
    .nav-auth-register {
      background: #00d4aa;
      color: #080e1c;
    }
    .nav-auth-register:hover {
      background: #00b894;
      opacity: .92;
    }

    /* ── Mobile: stack vertically inside any open mobile menu ── */
    @media (max-width: 680px) {
      #nav-auth {
        flex-direction: column;
        align-items: stretch;
        width: 100%;
        gap: 4px;
        margin-top: 4px;
      }
      .nav-auth-btn {
        justify-content: center;
        padding: 10px 14px;
        border-radius: 10px;
        font-size: .85rem;
      }
    }
  `;
  document.head.appendChild(style);
}());