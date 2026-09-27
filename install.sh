#!/bin/sh
# psnb-obs 安装器
#
#   ./install.sh skill [目标目录]   安装技能（默认自动探测 Minis / Claude Code / OpenCode）
#   ./install.sh pi                 安装 Pi 插件（npm install + sync-data + build）
#   ./install.sh check              跑全部测试
#   ./install.sh all                全装 + 自检
#
# 零依赖：只用到 sh / python3 / node（Pi 插件需要 node）。

set -e

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

say() { printf '%s\n' "$*"; }
die() { printf '错误: %s\n' "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

# ------------------------------------------------------------------ 技能

install_skill() {
	dest="$1"
	if [ -z "$dest" ]; then
		if [ -d /var/minis/skills ]; then
			dest=/var/minis/skills/psnb-obs
		elif [ -d "$HOME/.claude/skills" ]; then
			dest="$HOME/.claude/skills/psnb-obs"
		elif [ -d "$HOME/.config/opencode/skills" ]; then
			dest="$HOME/.config/opencode/skills/psnb-obs"
		else
			dest="$HOME/.claude/skills/psnb-obs"
		fi
	fi

	mkdir -p "$dest"
	cp "$ROOT/skill/SKILL.md" "$dest/SKILL.md"

	# 附带 data/ + engine/ + bin/ 副本，让技能在安装位置也能独立运行
	if have python3; then
		rm -rf "$dest/data" "$dest/engine" "$dest/bin"
		mkdir -p "$dest/data"
		cp -r "$ROOT/data/." "$dest/data/"
		# 实测产物不入技能（含真实模型响应）
		rm -rf "$dest/data/learned_ops.json.bak"* 2>/dev/null
		mkdir -p "$dest/engine"
		cp -r "$ROOT/engine/psnb" "$dest/engine/psnb"
		cp -r "$ROOT/engine/tests" "$dest/engine/tests"
		find "$dest/engine" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null

		# 便捷入口：$PSNB/psnb <子命令>
		mkdir -p "$dest/bin"
		cat > "$dest/psnb" <<'WRAP'
#!/bin/sh
# psnb-obs 便捷入口 —— 等价于 python3 engine/psnb/cli.py
exec python3 "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/engine/psnb/cli.py" "$@"
WRAP
		chmod +x "$dest/psnb"
		cp "$ROOT/bin/evolve-demo.py" "$dest/bin/" 2>/dev/null
		cp "$ROOT/bin/demo.sh" "$dest/bin/" 2>/dev/null
		chmod +x "$dest/bin/"* 2>/dev/null
	fi

	say "[ok] 技能已安装: $dest"
	say "     入口: $dest/psnb <子命令>"
	say "     自检: $dest/psnb evolve --self-check"
}

# ------------------------------------------------------------------ Pi 插件

install_pi() {
	have node || die "缺 node，Pi 插件需要 Node >= 18"
	have npm || die "缺 npm"
	cd "$ROOT/pi"
	say "[*] npm install ..."
	npm install --no-audit --no-fund
	say "[*] 同步 data/ ..."
	node scripts/sync-data.mjs
	say "[*] 编译 ..."
	./node_modules/.bin/tsc -p tsconfig.json
	say "[ok] Pi 插件已就绪: $ROOT/pi"
	say "     安装到 Pi:  pi install git:<你的仓库地址>"
	say "     或本地链接: pi install $ROOT/pi"
	cd "$ROOT"
}

# ------------------------------------------------------------------ 自检

check() {
	have python3 || die "缺 python3"
	say "===== Python 引擎测试 ====="
	(cd "$ROOT/engine" && python3 -m unittest discover -s tests 2>&1 | tail -4)

	if have node && [ -d "$ROOT/pi/node_modules/typescript" ]; then
		say ""
		say "===== Pi 插件测试 ====="
		(cd "$ROOT/pi" && node scripts/sync-data.mjs >/dev/null && ./node_modules/.bin/tsc -p tsconfig.json && node --test dist/tests/*.test.js 2>&1 | grep -E '^# (tests|pass|fail)')
	else
		say "(跳过 Pi 插件测试：未装 typescript，跑 ./install.sh pi)"
	fi

	say ""
	say "===== 条令体积 ====="
	python3 "$ROOT/engine/psnb/cli.py" doctrine --sizes

	say ""
	say "===== 数据层自检 ====="
	python3 "$ROOT/engine/psnb/cli.py" evolve --self-check

	say ""
	say "===== 自进化闭环演示 ====="
	python3 "$ROOT/bin/evolve-demo.py" 2>&1 | tail -12
}

case "${1:-all}" in
	skill) shift; install_skill "$@" ;;
	pi) install_pi ;;
	check) check ;;
	all)
		install_skill
		install_pi
		say ""
		check
		;;
	*)
		say "用法: $0 [skill|pi|check|all]"
		exit 2
		;;
esac
