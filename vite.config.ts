// @ts-nocheck
import { resolve } from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const frontendDir = fileURLToPath(new URL(".", import.meta.url));
const projectRoot = resolve(frontendDir, "..");

function cuevionImapBridge(): Plugin {
  return {
    name: "cuevion-imap-bridge",
    configureServer(server) {
      server.middlewares.use("/api/inboxes/connect", (req, res) => {
        if (req.method !== "POST") {
          res.statusCode = 405;
          res.setHeader("Content-Type", "application/json");
          res.end(JSON.stringify({ ok: false, error: { code: "method_not_allowed" } }));
          return;
        }

        let body = "";

        req.on("data", (chunk) => {
          body += chunk.toString();
        });

        req.on("end", () => {
          const pythonPath = resolve(projectRoot, "venv", "bin", "python");
          const scriptPath = resolve(projectRoot, "imap_onboarding_bridge.py");
          const process = spawn(pythonPath, [scriptPath], {
            cwd: projectRoot,
          });
          let stdout = "";
          let stderr = "";

          process.stdout.on("data", (chunk) => {
            stdout += chunk.toString();
          });

          process.stderr.on("data", (chunk) => {
            stderr += chunk.toString();
          });

          process.on("error", (error) => {
            res.statusCode = 500;
            res.setHeader("Content-Type", "application/json");
            res.end(
              JSON.stringify({
                ok: false,
                error: {
                  code: "bridge_failed",
                  message: error.message,
                },
              }),
            );
          });

          process.on("close", (code) => {
            const normalizedOutput = stdout.trim();

            if (!normalizedOutput) {
              res.statusCode = 500;
              res.setHeader("Content-Type", "application/json");
              res.end(
                JSON.stringify({
                  ok: false,
                  error: {
                    code: "bridge_failed",
                    message: stderr.trim() || "Inbox bridge returned no data.",
                  },
                }),
              );
              return;
            }

            res.statusCode = code === 0 ? 200 : 400;
            res.setHeader("Content-Type", "application/json");
            res.end(normalizedOutput);
          });

          process.stdin.write(body);
          process.stdin.end();
        });
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), cuevionImapBridge()],
});
