import { NextRequest, NextResponse } from "next/server";

export function middleware(req: NextRequest) {
  const user = process.env.DASHBOARD_USER || "";
  const pass = process.env.DASHBOARD_PASS || "";
  if (!user || !pass) {
    return new NextResponse("Dashboard chưa cấu hình DASHBOARD_USER/PASS", { status: 503 });
  }
  const expected = "Basic " + Buffer.from(`${user}:${pass}`).toString("base64");
  if (req.headers.get("authorization") !== expected) {
    return new NextResponse("Cần đăng nhập", {
      status: 401,
      headers: { "WWW-Authenticate": 'Basic realm="Jenny Dashboard"' },
    });
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
