// Constant Playwright command; spawn arguments bypass shell quoting entirely.
import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const manifest = process.env.RENO_E2E_MANIFEST;
if (!manifest) throw new Error("RENO_E2E_MANIFEST is required");
const python = process.env.RENO_E2E_PYTHON || resolve(repo,
  process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const args = [resolve(repo, "scripts/serve_product_fixture.py"), "--manifest", resolve(manifest)];
if (process.env.RENO_E2E_DIST) args.push("--dist", resolve(process.env.RENO_E2E_DIST));
const child = spawn(python, args, { cwd: repo, env: process.env, stdio: "inherit", shell: false, windowsHide: true });
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => child.kill(signal));
child.on("error", (error) => { console.error(error.message); process.exitCode = 1; });
child.on("exit", (code) => { process.exitCode = code ?? 1; });
