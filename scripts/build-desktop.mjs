// Static Next export must NOT contain dynamic /api routes. FastAPI supplies
// those routes in the Windows package. Restore sources even if the build fails.
import { existsSync } from "node:fs";
import { cp, mkdir, rename, rm, rmdir } from "node:fs/promises";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("../", import.meta.url));
const api = path.join(root, "src/app/api");
const backupRoot = path.join(root, ".desktop-api-backup");
const backup = path.join(backupRoot, "api");
if (existsSync(backup)) throw new Error("A previous static build was interrupted. Restore .desktop-api-backup/api to src/app/api before retrying.");
await mkdir(backupRoot, { recursive: true });
await rename(api, backup);
try {
  const child = spawn(process.execPath, [path.join(root, "node_modules/next/dist/bin/next"), "build"], {
    cwd: root, stdio: "inherit", env: { ...process.env, DESKTOP_EXPORT: "1" },
  });
  const interrupt = () => child.kill("SIGINT");
  process.once("SIGINT", interrupt);
  const code = await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code) => resolve(code ?? 1));
  });
  process.removeListener("SIGINT", interrupt);
  process.exitCode = code;
  if (code === 0) {
    // Next 16 exports into its custom distDir; ship a conventional out/ bundle.
    await rm(path.join(root, "out"), { recursive: true, force: true });
    await cp(path.join(root, ".next-export"), path.join(root, "out"), { recursive: true });
    console.log("Desktop UI written to out/; FastAPI supplies the runtime API routes.");
  }
} finally {
  await rename(backup, api);
  await rmdir(backupRoot);
}
