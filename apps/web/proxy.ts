import { NextResponse, type NextRequest } from "next/server";

/**
 * Optimistic check only: send visitors without a session cookie to /login before the
 * page loads. The API checks every request properly; this is not the security boundary.
 */
export function proxy(request: NextRequest) {
  if (!request.cookies.has("stllpay_session")) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/dashboard/:path*", "/admin/:path*"],
};
