#!/usr/bin/env bash
# Huoyan 部署脚本
# 用法: sudo bash deploy/install.sh [--user huoyan] [--host 0.0.0.0] [--port 8001]

set -euo pipefail

# ── 默认参数 ──
INSTALL_USER="${SUDO_USER:-$USER}"
HOST="0.0.0.0"
PORT="8001"
INSTALL_DIR="/opt/huoyan"
SERVICE_NAME="huoyan"

# ── 解析参数 ──
while [[ $# -gt 0 ]]; do
    case "$1" in
        --user)   INSTALL_USER="$2"; shift 2 ;;
        --host)   HOST="$2";          shift 2 ;;
        --port)   PORT="$2";          shift 2 ;;
        -h|--help)
            echo "用法: sudo bash deploy/install.sh [--user USER] [--host HOST] [--port PORT]"
            exit 0
            ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "请使用 root 权限运行此脚本 (sudo)"
    exit 1
fi

echo "==> 部署参数"
echo "    用户:      ${INSTALL_USER}"
echo "    安装目录:  ${INSTALL_DIR}"
echo "    监听地址:  ${HOST}:${PORT}"
echo ""

# ── 创建系统用户 ──
if ! id "${INSTALL_USER}" &>/dev/null; then
    echo "==> 创建系统用户 ${INSTALL_USER}"
    useradd --system --no-create-home --shell /usr/sbin/nologin "${INSTALL_USER}"
fi

# ── 创建目录 ──
echo "==> 创建目录结构"
mkdir -p "${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}/reports"

# ── 复制项目文件 ──
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "${SCRIPT_DIR}" != "${INSTALL_DIR}" ]]; then
    echo "==> 复制项目文件"
    cp -r "${SCRIPT_DIR}/src"               "${INSTALL_DIR}/"
    cp -r "${SCRIPT_DIR}/pyproject.toml"    "${INSTALL_DIR}/"
fi

# ── 创建虚拟环境并安装 ──
echo "==> 创建虚拟环境并安装依赖"
python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install --upgrade pip --quiet
"${INSTALL_DIR}/venv/bin/pip" install "${INSTALL_DIR}/" --quiet

# ── 生成 systemd 服务文件 ──
echo "==> 生成 systemd 服务文件"
sed \
    -e "s|User=huoyan|User=${INSTALL_USER}|g" \
    -e "s|Group=huoyan|Group=${INSTALL_USER}|g" \
    -e "s|WorkingDirectory=/opt/huoyan|WorkingDirectory=${INSTALL_DIR}|g" \
    -e "s|ReadWritePaths=/opt/huoyan/reports|ReadWritePaths=${INSTALL_DIR}/reports|g" \
    -e "s|ExecStart=/opt/huoyan/venv/bin/huoyan web --host 0.0.0.0 --port 8001|ExecStart=${INSTALL_DIR}/venv/bin/huoyan web --host ${HOST} --port ${PORT}|g" \
    "${SCRIPT_DIR}/deploy/huoyan.service" \
    > /etc/systemd/system/${SERVICE_NAME}.service

# ── 设置权限 ──
echo "==> 设置文件权限"
chown -R "${INSTALL_USER}:${INSTALL_USER}" "${INSTALL_DIR}"
chmod 750 "${INSTALL_DIR}"
chmod 755 "${INSTALL_DIR}/reports"

# ── 启用并启动服务 ──
echo "==> 启用并启动服务"
systemctl daemon-reload
systemctl enable ${SERVICE_NAME}
systemctl restart ${SERVICE_NAME}

echo ""
echo "==> 部署完成"
echo "    服务状态:  systemctl status ${SERVICE_NAME}"
echo "    查看日志:  journalctl -u ${SERVICE_NAME} -f"
echo "    访问地址:  http://${HOST}:${PORT}"
