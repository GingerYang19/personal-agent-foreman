#!/bin/bash
# 构建监工台按键注入组件
#
# 产物两部分：
#   SendHelper.app   稳定外壳，TCC 责任进程，授权绑定其二进制哈希 —— 尽量不重建
#   send_logic.scpt  真实逻辑，由外壳在自身进程内 load+run，改动不影响授权
#
# 关键顺序：osacompile 编译 → 改 Info.plist → 重新签名。
# 若签名后再改 plist 会破坏签名封印（codesign --verify 报 invalid Info.plist），
# macOS 将直接拒绝该 App 的辅助功能/自动化授权，且不弹出任何授权对话框。
#
# 用法：
#   ./build_sendhelper.sh          仅重编逻辑（推荐，不需重新授权）
#   ./build_sendhelper.sh --app    连外壳一起重建（会失效授权，需重新勾选）
set -e

cd "$(dirname "$0")"
BID="com.personal-hub.sendhelper"
APP="SendHelper.app"

# 逻辑脚本：随时可重编，不影响 TCC 授权
osacompile -o send_logic.scpt send_logic.applescript
echo "send_logic.scpt 已更新 ✓"

if [ "$1" = "--app" ] || [ ! -d "$APP" ]; then
    rm -rf "$APP"
    osacompile -o "$APP" send_helper.applescript
    # 稳定 Bundle ID：辅助功能列表按此识别 App，缺失则不显示在授权列表中
    plutil -replace CFBundleIdentifier -string "$BID" "$APP/Contents/Info.plist"
    plutil -replace CFBundleName -string "SendHelper" "$APP/Contents/Info.plist"
    # 改完 plist 必须重新签名，且签名标识与 Bundle ID 一致
    codesign --force --deep --sign - --identifier "$BID" "$APP"

    echo "--- 外壳签名校验 ---"
    codesign --verify --verbose "$APP" && echo "签名有效 ✓"
    codesign -dv "$APP" 2>&1 | grep -E "^Identifier|^Signature"
    echo ""
    echo "外壳已重建，需重新授权（重建会使旧授权失效）："
    echo "  系统设置 → 隐私与安全性 → 辅助功能 → 移除旧 SendHelper 条目后重新添加并勾选"
else
    echo "外壳未改动，无需重新授权。如需重建外壳请加 --app"
fi
