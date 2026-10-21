import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// CutVoke 前端构建配置。
// 产物统一放在 .artifacts/web，由本机服务映射：
//   - .artifacts/web/index.html 作为入口
//   - /assets/*.js|.css 由后端扩展支持
// 因此：
//   - base 固定为 '/'（绝对路径根相对，不能带相对 ./assets）
//   - 不用 history 路由（刷新会 404），前端用单视图状态切换
export default defineConfig({
  root: ".",
  base: "/",
  plugins: [react()],
  build: {
    outDir: "../../.artifacts/web",
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
