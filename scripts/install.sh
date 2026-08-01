#!/bin/bash
# ============================================================
# LightMail 一键部署脚本
# 轻量级邮局系统 - DN42 双栈支持
# ============================================================

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════╗"
echo "║    LightMail 轻量级邮局系统              ║"
echo "║    DN42 + 公网双栈支持                   ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"

# 检查是否 root
if [ "$EUID" -ne 0 ]; then 
  echo -e "${YELLOW}请使用 root 权限运行此脚本${NC}"
  exit 1
fi

# 获取配置
read -p "邮件域名 (如 yourdomain.dn42): " MAIL_DOMAIN
read -p "DN42 IPv4 地址: " DN42_IPV4
read -p "管理员密码: " -s ADMIN_PASS
echo

# 更新系统
echo -e "\n${GREEN}[1/6] 更新系统...${NC}"
apt update -y
apt install -y curl wget gnupg2 ca-certificates

# 安装 Postfix + Dovecot
echo -e "\n${GREEN}[2/6] 安装邮件服务 (Postfix + Dovecot)...${NC}"
DEBIAN_FRONTEND=noninteractive apt install -y postfix dovecot-core dovecot-imapd dovecot-pop3d dovecot-lmtpd sqlite3

# 安装 Python 依赖
echo -e "\n${GREEN}[3/6] 安装 Python 环境...${NC}"
apt install -y python3 python3-pip python3-venv

# 创建虚拟环境
python3 -m venv /opt/lightmail/venv
source /opt/lightmail/venv/bin/activate
pip install -r /opt/lightmail/requirements.txt

# 配置 Postfix
echo -e "\n${GREEN}[4/6] 配置 Postfix...${NC}"

# 创建 vmail 用户
groupadd -g 5000 vmail 2>/dev/null || true
useradd -g vmail -u 5000 vmail -d /var/mail/vmail -m -s /usr/sbin/nologin 2>/dev/null || true
mkdir -p /var/mail/vmail
chown -R vmail:vmail /var/mail/vmail

# 复制配置
cp /opt/lightmail/config/postfix/main.cf /etc/postfix/main.cf
cp /opt/lightmail/config/postfix/dn42_whitelist.cidr /etc/postfix/dn42_whitelist.cidr
cp /opt/lightmail/config/postfix/transport /etc/postfix/transport
cat /opt/lightmail/config/postfix/master.cf.append >> /etc/postfix/master.cf

# 替换域名
sed -i "s/yourdomain.dn42/$MAIL_DOMAIN/g" /etc/postfix/main.cf
sed -i "s/172.20.xx.xx/$DN42_IPV4/g" /etc/postfix/main.cf
sed -i "s/172.20.xx.xx/$DN42_IPV4/g" /etc/postfix/master.cf

# 生成映射
postmap /etc/postfix/dn42_whitelist.cidr
postmap /etc/postfix/transport
touch /etc/postfix/vmailbox
postmap /etc/postfix/vmailbox
touch /etc/postfix/virtual
postmap /etc/postfix/virtual

# 配置 Dovecot
echo -e "\n${GREEN}[5/6] 配置 Dovecot...${NC}"
cat /opt/lightmail/config/dovecot.conf > /etc/dovecot/dovecot.conf

# 生成 SSL 证书（自签名，生产环境用 DN42 CA）
echo -e "\n${GREEN}[6/6] 生成 SSL 证书...${NC}"
mkdir -p /etc/ssl/private
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /etc/ssl/private/mail.$MAIL_DOMAIN.key \
  -out /etc/ssl/certs/mail.$MAIL_DOMAIN.crt \
  -subj "/CN=mail.$MAIL_DOMAIN" 2>/dev/null

# 初始化 LightMail 数据库
export LIGHTMAIL_DOMAIN=$MAIL_DOMAIN
export LIGHTMAIL_DN42_IPV4=$DN42_IPV4
cd /opt/lightmail/app
python3 -c "
import main
main.init_db()
print('数据库初始化完成')
"

# 启动服务
echo -e "\n${GREEN}启动服务...${NC}"
systemctl restart postfix
systemctl restart dovecot

# 创建 systemd 服务
cat > /etc/systemd/system/lightmail.service << EOF
[Unit]
Description=LightMail Web Service
After=network.target postfix.service dovecot.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/lightmail
Environment="LIGHTMAIL_DOMAIN=$MAIL_DOMAIN"
Environment="LIGHTMAIL_DN42_IPV4=$DN42_IPV4"
ExecStart=/opt/lightmail/venv/bin/python /opt/lightmail/app/main.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable lightmail
systemctl start lightmail

# 完成
echo -e "\n${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║    安装完成！                            ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo "📧 Webmail:    http://$DN42_IPV4:8025"
echo "⚙️  管理后台:  http://$DN42_IPV4:8025/admin"
echo "📡 SMTP:       $DN42_IPV4:587 (STARTTLS)"
echo "📥 IMAP:       $DN42_IPV4:143 (STARTTLS)"
echo ""
echo "管理员: admin@$MAIL_DOMAIN / $ADMIN_PASS"
echo ""
echo "查看日志: journalctl -u lightmail -f"
