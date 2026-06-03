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
    transition: all .2s ease;
    line-height: 1.4;
  }

  /* ── Logged-in: logout button ── */
  .nav-auth-logout {
    background: rgba(255, 61, 77, .08);
    color: #FF3D4D;
    border: 1px solid rgba(255, 61, 77, .18);
  }

  .nav-auth-logout:hover {
    background: rgba(255, 61, 77, .16);
    color: #ffffff;
    box-shadow: 0 0 12px rgba(255, 61, 77, .25);
  }

  /* ── Logged-out: login link ── */
  .nav-auth-login {
    background: rgba(255,255,255,.04);
    color: rgba(255,255,255,.75);
    border: 1px solid rgba(255,255,255,.08);
  }

  .nav-auth-login:hover {
    background: rgba(13, 71, 255, .12);
    border-color: rgba(13, 71, 255, .35);
    color: #0D47FF;
    box-shadow: 0 0 12px rgba(13, 71, 255, .20);
  }

  /* ── Logged-out: register link (World Cup accent) ── */
  .nav-auth-register {
    background: linear-gradient(
      135deg,
      #0D47FF 0%,
      #7B2CFF 100%
    );
    color: #ffffff;
    box-shadow: 0 0 15px rgba(13, 71, 255, .25);
  }

  .nav-auth-register:hover {
    transform: translateY(-1px);
    box-shadow:
      0 0 20px rgba(13, 71, 255, .35),
      0 0 30px rgba(123, 44, 255, .20);
    opacity: 1;
  }

  /* ── Mobile ── */
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