import { type NextRequest, NextResponse } from "next/server";

import { PUBLIC_PATHS } from "./lib/auth";

/**
 * Signed-out visitors are sent to /login before any console page renders. The console layout
 * then checks the session and 2FA state with the API (a cookie alone proves nothing).
 */
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const isPublic = PUBLIC_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`));
  if (isPublic || request.cookies.has("sessionid")) return NextResponse.next();

  const login = new URL("/login", request.url);
  if (pathname !== "/") login.searchParams.set("next", `${pathname}${search}`);
  return NextResponse.redirect(login);
}

export const config = {
  // Everything except the API proxy, Next.js internals and static files.
  matcher: ["/((?!api/|_next/|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|ico|webp|txt)$).*)"],
};
