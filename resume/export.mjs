import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import puppeteer from "puppeteer-core";

const root = path.dirname(fileURLToPath(import.meta.url));
const html = pathToFileURL(path.join(root, "index.html")).href;
const outDir = path.join(root, "output");
const chrome = process.env.CHROME_PATH || "/usr/local/bin/google-chrome";

await mkdir(outDir, { recursive: true });

const browser = await puppeteer.launch({
  executablePath: chrome,
  headless: true,
  args: ["--no-sandbox", "--disable-gpu", "--font-render-hinting=medium"],
});

try {
  const page = await browser.newPage();
  await page.goto(html, { waitUntil: "networkidle0", timeout: 90_000 });
  await page.evaluate(() => document.fonts.ready);
  await page.emulateMediaType("print");
  const metrics = await page.$eval(".page", (el) => {
    const style = getComputedStyle(el);
    const h1 = el.querySelector("h1");
    const body = el.querySelector(".job-lead");
    const method = el.querySelector(".method");
    return {
      clientHeight: el.clientHeight,
      scrollHeight: el.scrollHeight,
      fonts: {
        h1: h1 && getComputedStyle(h1).fontFamily,
        body: body && getComputedStyle(body).fontFamily,
        method: method && getComputedStyle(method).fontFamily,
      },
      lastFooterTop: el.querySelector(".colophon")?.getBoundingClientRect().top,
      lastSec: [...el.querySelectorAll(".sec")].map((s) => ({
        cls: s.className,
        top: Math.round(s.getBoundingClientRect().top),
        bottom: Math.round(s.getBoundingClientRect().bottom),
        height: Math.round(s.getBoundingClientRect().height),
      })),
    };
  });
  console.log(JSON.stringify(metrics, null, 2));

  const pdfPath = path.join(outDir, "resume-zh.pdf");
  await page.pdf({
    path: pdfPath,
    format: "A4",
    printBackground: true,
    preferCSSPageSize: true,
    margin: { top: "0", right: "0", bottom: "0", left: "0" },
  });

  await page.setViewport({ width: 794, height: 1123, deviceScaleFactor: 2 });
  await page.screenshot({
    path: path.join(outDir, "preview.png"),
    type: "png",
    fullPage: false,
    clip: { x: 0, y: 0, width: 794, height: 1123 },
  });

  console.log(`wrote ${pdfPath}`);
} finally {
  await browser.close();
}
