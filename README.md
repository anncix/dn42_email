# ✉️ LightMail - 轻量级邮局系统

> 极简设计 · 快速部署 · DN42 原生支持 · 公网可控互通

LightMail 是一款专为中小规模场景设计的轻量级邮局系统，采用 **FastAPI + SQLite + Postfix + Dovecot** 技术栈，代码精简、依赖最少、即开即用。

## ✨ 特性

- 🚀 **极简架构** - 单文件核心代码，SQLite 零配置数据库
- 🌐 **DN42 原生** - 深度适配 DN42 网络，私有地址白名单，内部 CA 信任
- 🔗 **公网互通** - 三种互通模式，可控转发，速率限制
- 🎨 **现代化 UI** - 蓝白色调，响应式 Webmail，管理后台
- 📱 **多端支持** - 标准 SMTP/IMAP，兼容所有邮件客户端
- 🔌 **便捷接入** - RESTful API，可无缝集成现有系统
- 💾 **轻量高效** - 基础内存 < 50MB，支持 100-500 用户，极限 3000+

## 🚀 快速开始

### 方式一：Docker 一键启动（推荐）

```bash
# 克隆项目
git clone <repo-url>
cd lightmail

# 启动
docker compose up -d
```

访问：http://localhost:8025  
默认账号：`admin@example.dn42` / `admin123`

### 方式二：本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 启动
python app/main.py
```

### 方式三：一键部署脚本（生产环境）

```bash
# 在 Debian/Ubuntu 服务器上执行
curl -sSL https://<your-domain>/install.sh | bash
```

## 📁 项目结构

```
lightmail/
├── app/
│   └── main.py              # 核心应用（单文件）
├── templates/
│   ├── index.html           # Webmail 前端
│   ├── admin.html           # 管理后台
│   └── login.html           # 登录页
├── static/                  # 静态资源
├── config/
│   ├── postfix/             # Postfix 配置
│   ├── dovecot.conf         # Dovecot 配置
│   └── dn42-guide.md        # DN42 配置指南
├── scripts/
│   └── install.sh           # 一键部署脚本
├── data/                    # 数据目录（运行时生成）
├── requirements.txt         # Python 依赖
├── docker-compose.yml       # Docker 编排
├── Dockerfile               # Docker 镜像
└── .env.example             # 环境变量示例
```

## ⚙️ 配置说明

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LIGHTMAIL_DOMAIN` | example.dn42 | 邮件域名 |
| `LIGHTMAIL_SECRET` | change-me | 密钥（生产环境务必修改） |
| `LIGHTMAIL_DB` | ./data/lightmail.db | 数据库路径 |
| `LIGHTMAIL_SMTP_HOST` | 127.0.0.1 | SMTP 服务器地址 |
| `LIGHTMAIL_SMTP_PORT` | 587 | SMTP 端口 |
| `LIGHTMAIL_IMAP_HOST` | 127.0.0.1 | IMAP 服务器地址 |
| `LIGHTMAIL_IMAP_PORT` | 143 | IMAP 端口 |
| `LIGHTMAIL_DN42` | 1 | 启用 DN42 支持 |
| `LIGHTMAIL_DN42_IPV4` | - | DN42 IPv4 地址 |
| `LIGHTMAIL_PUBLIC` | 0 | 启用公网互通 |

### DN42 配置

详细配置请参考 [config/dn42-guide.md](config/dn42-guide.md)

**关键步骤：**

1. 配置 Postfix 监听 DN42 地址
2. 添加 DN42 地址白名单
3. 配置 DN42 专用传输通道
4. 设置 DNS（MX/SPF/DKIM/DMARC）
5. 安装 DN42 内部 CA 证书

### 公网互通模式

| 模式 | 入站 | 出站 | 配置 |
|------|------|------|------|
| 纯 DN42 | ❌ | ❌ | `LIGHTMAIL_PUBLIC=0` |
| 受控转发 | ⚠️ 白名单 | ⚠️ 白名单 | 转发规则配置 |
| 完全互通 | ✅ | ✅ | `LIGHTMAIL_PUBLIC=1` |

## 🔌 API 接入

### 认证

```bash
# 登录获取 token
curl -X POST http://localhost:8025/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin@example.dn42","password":"admin123"}'
```

### 发送邮件

```bash
curl -X POST http://localhost:8025/api/mail/send \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "to": ["user@target.dn42"],
    "subject": "测试邮件",
    "body": "这是一封测试邮件",
    "network": "auto"
  }'
```

`network` 参数可选值：
- `auto` - 自动选择（.dn42 域名走 DN42，其他走公网）
- `dn42` - 强制 DN42 网络
- `public` - 强制公网

### 获取 DN42 状态

```bash
curl http://localhost:8025/api/dn42/status
```

## 📊 性能指标

| 指标 | 数值 |
|------|------|
| 基础内存占用 | ~50 MB |
| 单节点支持用户 | 100-500（活跃）|
| 极限用户承载 | 3000+ |
| 邮件处理能力 | 1000+/小时 |
| 启动时间 | < 3 秒 |

## 🔒 安全

- ✅ 全链路 TLS 加密
- ✅ SPF / DKIM / DMARC 完整支持
- ✅ 密码哈希存储
- ✅ 登录失败锁定
- ✅ DN42 地址白名单
- ✅ 公网出站速率限制

## 🤝 与 Flask 兼容

如果你更习惯 Flask，可以：

1. 使用 Flask 风格的路由装饰器
2. 调用 `app.router` 注册 Flask 蓝图
3. 使用 `flask-to-fastapi` 迁移工具

核心 API 保持 RESTful 标准，无论用哪个框架都能无缝接入。

## 📝 License

MIT License
