#!/usr/bin/env bash
# Huoyan 卸载脚本
# 用法: sudo bash deploy/uninstall.sh

set -euo pipefail

SERVICE_NAME="huoyan"
INSTALL_DIR="/opt/huoyan"

if [[ $EUID -ne 0 ]]; then
    echo "请使用 root 权限运行此脚本 (sudo)"
    exit 1
fi

echo "==> 停止服务"
systemctl stop ${SERVICE_NAME} 2>/dev/null || true
systemctl disable ${SERVICE_NAME} 2>/dev/null || true

echo "==> 移除 systemd 服务文件"
rm -f /etc/systemd/system/${SERVICE_NAME}.service
systemctl daemon-reload

echo "==> 删除安装目录: ${INSTALL_DIR}"
read -r -p "    确认删除? [y/N] " confirm
if [[ "${confirm}" =~ ^[Yy]$ ]]; then
    rm -rf "${INSTALL_DIR}"
    echo "    已删除"
else
    echo "    已跳过"
fi

echo "==> 移除系统用户"
if id huoyan &>/dev/null; then
    userdel huoyan 2>/dev/null || true
fi

echo "==> 卸载完成"
