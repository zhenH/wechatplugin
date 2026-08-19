#!/usr/bin/env bash
# 多人格群聊平台 — Ubuntu/Debian 服务器一键部署脚本
#
# 用法:  sudo bash setup.sh [端口] [项目目录]
# 示例:  sudo bash setup.sh 8123 /opt/wechatplugin
#   - 端口默认 8000；若与已有项目冲突，换一个（如 8123）
#   - 项目目录默认当前目录
set -euo pipefail

PORT="${1:-8000}"
APP_DIR="${2:-$(pwd)}"
SERVICE="wechatplugin"
cd "$APP_DIR"

echo "==> 1/6 检查端口 $PORT 是否被占用"
if ss -tlnp 2>/dev/null | grep -q ":$PORT "; then
  echo "❌ 端口 $PORT 已被占用（可能正是你已有的项目）！"
  echo "   换一个端口重试，例如: sudo bash setup.sh 8123"
  exit 1
fi
echo "   端口可用 ✓"

echo "==> 2/6 安装 uv（Python 环境管理，无则装）"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv --version

echo "==> 3/6 创建虚拟环境并安装依赖"
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt

echo "==> 4/6 配置 .env"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "   ⚠️ 已生成 .env，请先编辑它（必改两项）："
  echo "       nano $APP_DIR/.env"
  echo "       - LLM_API_KEY=sk-你的DeepSeek key"
  echo "       - ACCESS_PASSWORD=访问密码（公网必设）"
  echo "   然后重新运行本脚本继续。"
  exit 0
fi
echo "   .env 已存在 ✓（如未填 key/密码，请编辑后再重启服务）"

echo "==> 5/6 安装 systemd 服务（开机自启 + 崩溃自动重启）"
cat > /etc/systemd/system/${SERVICE}.service <<EOF
[Unit]
Description=多人格群聊平台 (Multi-Persona Chat)
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python run.py
Environment=HOST=0.0.0.0
Environment=PORT=${PORT}
Restart=always
RestartSec=3
# 内存保护：2G 机器上限给 1.5G，防止内存泄漏拖垮整机
MemoryMax=1536M

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable ${SERVICE}
systemctl restart ${SERVICE}

echo "==> 6/6 放行防火墙端口"
if command -v ufw >/dev/null 2>&1; then
  ufw allow ${PORT}/tcp >/dev/null 2>&1 || true
  echo "   ufw 已放行 ${PORT}/tcp"
fi

sleep 2
if systemctl is-active --quiet ${SERVICE}; then
  echo ""
  echo "✅ 部署完成！"
  echo "   访问:  http://服务器IP:${PORT}"
  echo "   查看日志:  journalctl -u ${SERVICE} -f"
  echo "   重启服务:  sudo systemctl restart ${SERVICE}"
  echo "   备份数据:  cp ${APP_DIR}/chat.db 和 ${APP_DIR}/app/static/avatars/"
else
  echo "❌ 服务启动失败，查看日志: journalctl -u ${SERVICE} -e"
fi
