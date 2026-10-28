/** Small inline icons (24x24, stroke). */
const base = {
  width: 20,
  height: 20,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

export const icons = {
  dashboard: (
    <svg {...base}>
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
    </svg>
  ),
  clients: (
    <svg {...base}>
      <path d="M3 21V7l9-4 9 4v14" />
      <path d="M9 21v-6h6v6" />
      <path d="M8 10h.01M12 10h.01M16 10h.01" />
    </svg>
  ),
  industry: (
    <svg {...base}>
      <path d="M4 6h16M4 12h16M4 18h10" />
      <circle cx="19" cy="18" r="2" />
    </svg>
  ),
  whatsapp: (
    <svg {...base}>
      <path d="M21 12a9 9 0 0 1-13.5 7.8L3 21l1.2-4.5A9 9 0 1 1 21 12Z" />
      <path d="M9 10h6M9 14h4" />
    </svg>
  ),
  requests: (
    <svg {...base}>
      <path d="M4 5h16v11H8l-4 4V5Z" />
      <path d="M12 8v3M12 13.5v.01" />
    </svg>
  ),
  audit: (
    <svg {...base}>
      <path d="M9 4h6l1 2h3v15H5V6h3l1-2Z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  ),
  menu: (
    <svg {...base}>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </svg>
  ),
  close: (
    <svg {...base}>
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  ),
  signOut: (
    <svg {...base}>
      <path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10" />
    </svg>
  ),
};
