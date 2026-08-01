# LightMail DN42 配置指南

## DN42 网络适配

### 1. Postfix DN42 配置

```bash
# /etc/postfix/main.cf
# 监听 DN42 地址
inet_interfaces = 127.0.0.1, 172.20.xx.xx

# 允许私有地址投递（关键！）
# 默认 Debian 配置会拒绝 RFC1918 地址
smtpd_recipient_restrictions =
    permit_mynetworks
    permit_sasl_authenticated
    check_client_access cidr:/etc/postfix/dn42_whitelist.cidr
    reject_unauth_destination

# DN42 专用传输通道
transport_maps = hash:/etc/postfix/transport

# HELO 名称
smtp_helo_name = mail.yourdomain.dn42

# TLS 配置 - 使用 DN42 内部 CA 证书
smtp_tls_CAfile = /etc/ssl/certs/dn42-root-ca.pem
smtp_tls_security_level = may
smtpd_tls_cert_file = /etc/ssl/certs/mail.yourdomain.dn42.crt
smtpd_tls_key_file = /etc/ssl/private/mail.yourdomain.dn42.key
```

### 2. DN42 白名单

```bash
# /etc/postfix/dn42_whitelist.cidr
# DN42 IPv4
172.20.0.0/14       OK
10.0.0.0/8          OK

# DN42 IPv6
fc00::/7            OK
fd00::/8            OK
```

执行：`postmap /etc/postfix/dn42_whitelist.cidr`

### 3. DN42 传输通道

```bash
# /etc/postfix/transport
.dn42               dn42:
.neonetwork         dn42:
.hack               dn42:
```

执行：`postmap /etc/postfix/transport`

```bash
# /etc/postfix/master.cf
dn42 unix - - n - - smtp
 -o smtp_bind_address=172.20.xx.xx
 -o smtp_bind_address6=fdxx:xxxx:xxxx::1
 -o smtp_helo_name=mail.yourdomain.dn42
 -o syslog_name=postfix-dn42
```

### 4. Dovecot DN42 配置

```bash
# /etc/dovecot/conf.d/10-master.conf
service imap-login {
  inet_listener imap {
    address = 127.0.0.1, 172.20.xx.xx
    port = 143
  }
  inet_listener imaps {
    address = 127.0.0.1, 172.20.xx.xx
    port = 993
    ssl = yes
  }
}
```

### 5. DNS 记录配置

```
; 在你的 DN42 域名 DNS 中添加
mail.yourdomain.dn42.        A      172.20.xx.xx
mail.yourdomain.dn42.        AAAA   fdxx:xxxx:xxxx::1
yourdomain.dn42.             MX     10 mail.yourdomain.dn42.

; SPF
yourdomain.dn42.             TXT    "v=spf1 a mx ip4:172.20.xx.xx ip6:fdxx:xxxx:xxxx::1 -all"

; DKIM (使用 Rspamd 生成)
default._domainkey.yourdomain.dn42.  TXT  "v=DKIM1; k=rsa; p=..."

; DMARC
_dmarc.yourdomain.dn42.      TXT    "v=DMARC1; p=none; rua=mailto:postmaster@yourdomain.dn42"
```

## 公网互通配置

### 三种模式

| 模式 | 入站 | 出站 | 配置 |
|------|------|------|------|
| 纯 DN42 | ❌ | ❌ | `LIGHTMAIL_PUBLIC=0` |
| 受控转发 | ⚠️ | ⚠️ | 白名单 + 速率限制 |
| 完全互通 | ✅ | ✅ | `LIGHTMAIL_PUBLIC=1` |

### 受控转发规则

在 LightMail 管理后台配置：

```
规则1: 允许管理员向公网发送
  方向: 出站
  用户组: admins
  动作: 允许
  优先级: 10

规则2: 公网白名单域名可发入
  方向: 入站
  发件人域名: trusted-partner.com
  动作: 允许
  优先级: 20

规则3: 默认拒绝公网入站
  方向: 入站
  匹配: *
  动作: 拒绝
  优先级: 100
```

### 速率限制

```python
# 默认公网出站限制
DAILY_PUBLIC_LIMIT = 100  # 每天 100 封
HOURLY_PUBLIC_LIMIT = 20  # 每小时 20 封
```

### 反垃圾配置

使用 Rspamd 作为反垃圾引擎：

- SPF 验证
- DKIM 签名验证
- DMARC 策略执行
- Bayesian 贝叶斯过滤
- RBL 黑名单检查
- 附件病毒扫描（ClamAV）

## 智能网络选择

LightMail 自动选择最优发送网络：

```
收件人全部是 .dn42 域名 → DN42 网络
收件人包含公网域名   → 公网网络
用户手动指定         → 按用户选择
```

邮件头中会添加 `X-LightMail-Network: dn42/public` 标记。
