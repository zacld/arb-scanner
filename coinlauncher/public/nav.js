// Shared top navigation, injected into every dashboard page. Carries the
// current project selection (?project=...) across links so switching pages
// doesn't lose context.
(function () {
  const PAGES = [
    { href: "overview.html", label: "Overview" },
    { href: "index.html", label: "Launch" },
    { href: "liquidity.html", label: "Liquidity" },
    { href: "console.html", label: "Wallets" },
    { href: "trading.html", label: "Trading" },
    { href: "analytics.html", label: "Analytics" },
    { href: "socials.html", label: "Socials" },
  ];

  const params = new URLSearchParams(location.search);
  const projectId = params.get("project") || params.get("launch") || "";
  const currentFile = location.pathname.split("/").pop() || "index.html";

  const style = document.createElement("style");
  style.textContent = `
    #appNav { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 20px; border-bottom: 1px solid #8883; padding-bottom: 10px; }
    #appNav a { padding: 6px 12px; border-radius: 6px; text-decoration: none; color: inherit; font-size: 0.85rem; opacity: 0.7; }
    #appNav a.active { background: #6c5ce7; color: white; opacity: 1; font-weight: 600; }
    #appNav a:hover { opacity: 1; }
  `;
  document.head.appendChild(style);

  const nav = document.createElement("div");
  nav.id = "appNav";
  nav.innerHTML = PAGES.map((p) => {
    const href = projectId ? `${p.href}?project=${encodeURIComponent(projectId)}` : p.href;
    const active = p.href === currentFile ? "active" : "";
    return `<a href="${href}" class="${active}">${p.label}</a>`;
  }).join("");

  document.body.insertBefore(nav, document.body.firstChild);
})();
