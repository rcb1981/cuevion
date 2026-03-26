// @ts-nocheck
import { resolve } from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
var frontendDir = fileURLToPath(new URL(".", import.meta.url));
var projectRoot = resolve(frontendDir, "..");
function cuevionImapBridge() {
    return {
        name: "cuevion-imap-bridge",
        configureServer: function (server) {
            server.middlewares.use("/api/inboxes/connect", function (req, res) {
                if (req.method !== "POST") {
                    res.statusCode = 405;
                    res.setHeader("Content-Type", "application/json");
                    res.end(JSON.stringify({ ok: false, error: { code: "method_not_allowed" } }));
                    return;
                }
                var body = "";
                req.on("data", function (chunk) {
                    body += chunk.toString();
                });
                req.on("end", function () {
                    var pythonPath = resolve(projectRoot, "venv", "bin", "python");
                    var scriptPath = resolve(projectRoot, "imap_onboarding_bridge.py");
                    var process = spawn(pythonPath, [scriptPath], {
                        cwd: projectRoot,
                    });
                    var stdout = "";
                    var stderr = "";
                    process.stdout.on("data", function (chunk) {
                        stdout += chunk.toString();
                    });
                    process.stderr.on("data", function (chunk) {
                        stderr += chunk.toString();
                    });
                    process.on("error", function (error) {
                        res.statusCode = 500;
                        res.setHeader("Content-Type", "application/json");
                        res.end(JSON.stringify({
                            ok: false,
                            error: {
                                code: "bridge_failed",
                                message: error.message,
                            },
                        }));
                    });
                    process.on("close", function (code) {
                        var normalizedOutput = stdout.trim();
                        if (!normalizedOutput) {
                            res.statusCode = 500;
                            res.setHeader("Content-Type", "application/json");
                            res.end(JSON.stringify({
                                ok: false,
                                error: {
                                    code: "bridge_failed",
                                    message: stderr.trim() || "Inbox bridge returned no data.",
                                },
                            }));
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
