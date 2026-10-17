import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";

const apiUrl = process.env.TECHCOMPASS_API_URL?.trim();
if (!apiUrl) {
  throw new Error("Set TECHCOMPASS_API_URL to the deployed API URL.");
}

const parsedApiUrl = new URL(apiUrl);
const isLocalDevelopmentApi = (
  parsedApiUrl.protocol === "http:" &&
  ["localhost", "127.0.0.1"].includes(parsedApiUrl.hostname)
);
if (parsedApiUrl.protocol !== "https:" && !isLocalDevelopmentApi) {
  throw new Error("TECHCOMPASS_API_URL must use HTTPS.");
}

mkdirSync("dist", { recursive: true });
for (const file of ["index.html", "app.js", "styles.css"]) {
  copyFileSync(file, `dist/${file}`);
}
writeFileSync(
  "dist/config.js",
  `window.TECHCOMPASS_API_URL = ${JSON.stringify(apiUrl.replace(/\/+$/, ""))};\n`,
);
