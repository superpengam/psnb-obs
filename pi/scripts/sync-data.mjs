#!/usr/bin/env node
/**
 * 把 ../data 同步进包内 data/ —— npm 包必须自带规则表，不能依赖仓库外的路径。
 * 幂等：只复制，不做别的。
 */
import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const pkgRoot = join(here, "..");
const src = join(pkgRoot, "..", "data");
const dst = join(pkgRoot, "data");

if (!existsSync(src)) {
	console.error(`[sync-data] 源目录不存在: ${src}`);
	process.exit(1);
}

rmSync(dst, { recursive: true, force: true });
mkdirSync(dst, { recursive: true });
cpSync(src, dst, { recursive: true });
console.log(`[sync-data] ${src} → ${dst}`);
