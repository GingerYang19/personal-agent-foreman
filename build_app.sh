#!/bin/bash
# 构建可独立安装的 AgentForeman.app + 发行 DMG（内嵌整个项目，拖入 /Applications 即用）
# 产物: dist/AgentForeman.app、dist/AgentForeman.dmg
# 运行数据目录: ~/Library/Application Support/AgentForeman（升级 App 不丢用户数据）
set -euo pipefail
cd "$(dirname "$0")"

DIST=dist
APP="${DIST}/AgentForeman.app"
rm -rf "${APP}"
mkdir -p "${DIST}"

# 0) 编译桌面窗口程序(AppKit + WKWebView, universal)到仓库根 App；无 swiftc 时沿用已提交的二进制
BIN="AgentForeman.app/Contents/MacOS/AgentForeman"
if command -v swiftc >/dev/null 2>&1; then
  echo "▸ 编译桌面窗口程序…"
  swiftc -O -target arm64-apple-macos11 -o /tmp/afm-arm64 AgentForemanApp.swift
  if swiftc -O -target x86_64-apple-macos11 -o /tmp/afm-x86_64 AgentForemanApp.swift 2>/dev/null; then
    /usr/bin/lipo -create -output "${BIN}" /tmp/afm-arm64 /tmp/afm-x86_64
  else
    cp /tmp/afm-arm64 "${BIN}"   # x86_64 工具链不可用时降级单架构
  fi
  rm -f /tmp/afm-arm64 /tmp/afm-x86_64
  /usr/bin/codesign --force -s - AgentForeman.app >/dev/null 2>&1 || true
fi

# 1) 复制 App 骨架（窗口程序 + 引导脚本 + Info.plist + 图标）
/usr/bin/ditto AgentForeman.app "${APP}"

# 2) 内嵌项目 payload（排除仓库元数据/运行时文件/个人数据/宣传素材/测试）
PAYLOAD="${APP}/Contents/Resources/app"
mkdir -p "${PAYLOAD}"
/usr/bin/rsync -a \
  --exclude '.git' --exclude '.gitignore' --exclude '.qoder' \
  --exclude '__pycache__' --exclude '.DS_Store' \
  --exclude 'AgentForeman.app' --exclude 'dist' --exclude 'build_app.sh' \
  --exclude 'AgentForemanApp.swift' \
  --exclude 'screenshots' --exclude 'tests' --exclude 'capture_*.py' \
  --exclude 'screenshot-v2.png' \
  --exclude 'server.log' --exclude 'server.err' --exclude 'send.log' \
  --exclude 'send_task.txt' --exclude 'send_result.txt' \
  --exclude 'journal.json' --exclude 'aliases.json' \
  ./ "${PAYLOAD}/"

# 内嵌 payload 后重新 ad-hoc 签名
/usr/bin/codesign --force -s - "${APP}" >/dev/null 2>&1 || true

echo "✅ 已生成 ${APP}"

# 3) 打包 DMG（含拖入安装的 Applications 软链）
DMG="${DIST}/AgentForeman.dmg"
STAGING="${DIST}/.dmg-staging"
rm -rf "${STAGING}" "${DMG}"
mkdir -p "${STAGING}"
/usr/bin/ditto "${APP}" "${STAGING}/AgentForeman.app"
ln -s /Applications "${STAGING}/Applications"
/usr/bin/hdiutil create -volname "Agent 监工台" -srcfolder "${STAGING}" -ov -format UDZO -quiet "${DMG}"
rm -rf "${STAGING}"

echo "✅ 已生成 ${DMG}"
echo "   分发: 发给他人后双击挂载，把 AgentForeman 拖入 Applications 即可（首次打开: 右键 → 打开）"
echo "   运行数据位于 ~/Library/Application Support/AgentForeman"
echo "   发话功能需为该目录下的 SendHelper.app 授权辅助功能"
