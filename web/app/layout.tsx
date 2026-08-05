import "./globals.css";
import Link from "next/link";

export const metadata = { title: "Jenny — LSR BOD Assistant" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi">
      <body>
        <div className="layout">
          <nav className="sidebar">
            <h1>🤖 Jenny</h1>
            <Link href="/">Tổng quan</Link>
            <Link href="/tools">Tool calls</Link>
            <Link href="/conversations">Hội thoại</Link>
            <Link href="/skills">Skills</Link>
            <Link href="/configs">Configs</Link>
            <Link href="/schedules">Lịch chạy</Link>
            <Link href="/meetings">Họp</Link>
          </nav>
          <main className="main">{children}</main>
        </div>
      </body>
    </html>
  );
}
