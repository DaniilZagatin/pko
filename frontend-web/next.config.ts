import type { NextConfig } from "next";

// `pko serve` (FastAPI) слушает отдельным процессом — по умолчанию
// http://127.0.0.1:8000 (backend/pko/cli.py::cmd_serve). В проде оба
// процесса обычно стоят за одним обратным прокси, поэтому переменная
// окружения нужна только для локальной разработки.
const backendUrl = process.env.PKO_BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/api/:path*` }];
  },
};

export default nextConfig;
