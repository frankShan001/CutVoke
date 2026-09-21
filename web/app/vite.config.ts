import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// CutVoke 前端构建配置。
// 产物输出到 ../dist（即 web/dist），由 Python stdlib http.server 精确映射：
//   - web/dist/index.html 作为入口
//   - /assets/*.js|.css 由后端扩展支持
// 因此：
//   - base 固定为 '/'（绝对路径根相对，不能带相对 ./assets）
//   - 不用 history 路由（刷新会 404），前端用单视图状态切换
export default defineConfig({
  root: ".",
  base: "/",
  plugins: [react()],
  build: {
    outDir: "../dist",
    emptyOutDir: true,
    assetsDir: "assets",
    sourcemap: false,
    // 结构与后端静态服务匹配：单入口 + 纯静态资源
    rollupOptions: {
      output: {
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash].[ext]",
      },
    },
  },
  server: {
    port: 5173,
    strictPort: false,
  },
});