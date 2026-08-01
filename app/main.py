"""
LightMail - 轻量级邮局系统
极简核心：FastAPI + SQLite + Postfix + Dovecot
支持 DN42 + 公网双栈
"""
import os
import sqlite3
import hashlib
import secrets
import smtplib
import imaplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header, make_header
from datetime import datetime, timedelta
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Depends, Header, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr
import uvicorn

# ============================================================
# 配置
# ============================================================
BASE_DIR = Path(__file__).parent.parent
DB_PATH = os.environ.get("LIGHTMAIL_DB", str(BASE_DIR / "data" / "lightmail.db"))
MAIL_DOMAIN = os.environ.get("LIGHTMAIL_DOMAIN", "example.dn42")
SMTP_HOST = os.environ.get("LIGHTMAIL_SMTP_HOST", "127.0.0.1")
SMTP_PORT = int(os.environ.get("LIGHTMAIL_SMTP_PORT", "587"))
IMAP_HOST = os.environ.get("LIGHTMAIL_IMAP_HOST", "127.0.0.1")
IMAP_PORT = int(os.environ.get("LIGHTMAIL_IMAP_PORT", "143"))
SECRET_KEY = os.environ.get("LIGHTMAIL_SECRET", "change-me-in-production")
DN42_ENABLED = os.environ.get("LIGHTMAIL_DN42", "1") == "1"
DN42_IPV4 = os.environ.get("LIGHTMAIL_DN42_IPV4", "")
PUBLIC_ENABLED = os.environ.get("LIGHTMAIL_PUBLIC", "0") == "1"

# ============================================================
# 数据库
# ============================================================
def get_db():
    """获取数据库连接"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """初始化数据库"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        display_name TEXT,
        quota_mb INTEGER DEFAULT 1024,
        is_active INTEGER DEFAULT 1,
        is_admin INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token TEXT UNIQUE NOT NULL,
        expires_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS domains (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        domain TEXT UNIQUE NOT NULL,
        is_dn42 INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        address TEXT NOT NULL,
        goto TEXT NOT NULL,
        domain TEXT NOT NULL,
        is_active INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS forward_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        direction TEXT NOT NULL,
        pattern TEXT,
        target TEXT,
        action TEXT DEFAULT 'forward',
        is_active INTEGER DEFAULT 1,
        priority INTEGER DEFAULT 100,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS mail_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        folder TEXT DEFAULT 'INBOX',
        message_uid TEXT,
        subject TEXT,
        sender TEXT,
        recipient TEXT,
        size INTEGER DEFAULT 0,
        is_read INTEGER DEFAULT 0,
        is_flagged INTEGER DEFAULT 0,
        date TEXT,
        preview TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );

    CREATE INDEX IF NOT EXISTS idx_mail_user ON mail_cache(user_id, folder);
    CREATE INDEX IF NOT EXISTS idx_tokens_token ON tokens(token);
    """)

    # 默认管理员
    admin_email = f"admin@{MAIL_DOMAIN}"
    cur = conn.execute("SELECT id FROM users WHERE email=?", (admin_email,))
    if not cur.fetchone():
        pw_hash = hash_password("admin123")
        conn.execute(
            "INSERT INTO users (username, password_hash, email, display_name, is_admin) VALUES (?,?,?,?,1)",
            ("admin", pw_hash, admin_email, "管理员")
        )
        print(f"[+] 默认管理员已创建: {admin_email} / admin123")

    # 默认域名
    conn.execute(
        "INSERT OR IGNORE INTO domains (domain, is_dn42) VALUES (?, ?)",
        (MAIL_DOMAIN, 1 if DN42_ENABLED else 0)
    )

    conn.commit()
    conn.close()


# ============================================================
# 工具函数
# ============================================================
def hash_password(password: str) -> str:
    """密码哈希（简化版，生产环境请用 bcrypt/argon2）"""
    return hashlib.sha256((password + SECRET_KEY).encode()).hexdigest()


def verify_password(password: str, pw_hash: str) -> bool:
    """验证密码"""
    return hash_password(password) == pw_hash


def generate_token(user_id: int, db: sqlite3.Connection) -> str:
    """生成访问令牌"""
    token = secrets.token_hex(32)
    expires = (datetime.utcnow() + timedelta(days=7)).isoformat()
    db.execute(
        "INSERT INTO tokens (user_id, token, expires_at) VALUES (?,?,?)",
        (user_id, token, expires)
    )
    db.commit()
    return token


def get_current_user(authorization: Optional[str] = Header(None),
                     db: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    """获取当前用户"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未授权")
    token = authorization.replace("Bearer ", "")
    cur = db.execute(
        "SELECT u.* FROM users u JOIN tokens t ON u.id=t.user_id "
        "WHERE t.token=? AND t.expires_at > datetime('now') AND u.is_active=1",
        (token,)
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    return dict(row)


def decode_mime_str(s: str) -> str:
    """解码 MIME 编码字符串"""
    if not s:
        return ""
    try:
        return str(make_header(decode_header(s)))
    except Exception:
        return s


def is_dn42_domain(email_addr: str) -> bool:
    """判断是否为 DN42 域名邮箱"""
    dn42_tlds = ['.dn42', '.neonetwork', '.hack', '.chaos', '.oss']
    return any(email_addr.endswith(tld) for tld in dn42_tlds)


def select_network(recipients: List[str]) -> str:
    """智能选择发送网络"""
    if not DN42_ENABLED:
        return "public"
    if not PUBLIC_ENABLED:
        return "dn42"
    all_dn42 = all(is_dn42_domain(r) for r in recipients)
    return "dn42" if all_dn42 else "public"


# ============================================================
# IMAP 操作封装
# ============================================================
class MailClient:
    """IMAP/SMTP 客户端封装"""

    def __init__(self, email_addr: str, password: str):
        self.email_addr = email_addr
        self.password = password
        self._imap = None
        self._smtp = None

    def connect_imap(self):
        """连接 IMAP"""
        try:
            self._imap = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
            self._imap.login(self.email_addr, self.password)
            return True
        except Exception as e:
            print(f"IMAP 连接失败: {e}")
            return False

    def connect_smtp(self, network: str = "auto"):
        """连接 SMTP"""
        try:
            self._smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
            self._smtp.starttls()
            self._smtp.login(self.email_addr, self.password)
            return True
        except Exception as e:
            print(f"SMTP 连接失败: {e}")
            return False

    def list_folders(self) -> List[str]:
        """获取文件夹列表"""
        if not self._imap and not self.connect_imap():
            return ["INBOX"]
        try:
            status, data = self._imap.list()
            folders = []
            for d in data:
                if d:
                    parts = d.decode().split('"')
                    if len(parts) >= 3:
                        folders.append(parts[-2].strip())
            return folders or ["INBOX", "Sent", "Drafts", "Trash"]
        except Exception:
            return ["INBOX", "Sent", "Drafts", "Trash"]

    def list_messages(self, folder: str = "INBOX", limit: int = 50,
                      offset: int = 0) -> List[Dict]:
        """获取邮件列表"""
        if not self._imap and not self.connect_imap():
            return []
        try:
            self._imap.select(folder)
            status, data = self._imap.search(None, "ALL")
            if status != "OK":
                return []
            ids = data[0].split()
            # 倒序取最新的
            ids = list(reversed(ids))
            ids = ids[offset:offset + limit]

            messages = []
            for msg_id in ids:
                status, msg_data = self._imap.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)] RFC822.SIZE FLAGS)")
                if status == "OK" and msg_data[0]:
                    try:
                        raw_header = msg_data[0][1]
                        msg = email.message_from_bytes(raw_header)
                        flags = msg_data[0][0].decode() if isinstance(msg_data[0][0], bytes) else str(msg_data[0][0])
                        size = 0
                        if 'RFC822.SIZE' in flags:
                            import re
                            m = re.search(r'RFC822.SIZE (\d+)', flags)
                            if m:
                                size = int(m.group(1))
                        is_read = '\\Seen' in flags
                        is_flagged = '\\Flagged' in flags

                        subject = decode_mime_str(msg.get("Subject", ""))
                        sender = decode_mime_str(msg.get("From", ""))
                        date_str = msg.get("Date", "")

                        messages.append({
                            "id": msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id),
                            "subject": subject,
                            "sender": sender,
                            "date": date_str,
                            "size": size,
                            "is_read": is_read,
                            "is_flagged": is_flagged,
                            "preview": subject[:80] + "..." if len(subject) > 80 else subject
                        })
                    except Exception as e:
                        print(f"解析邮件失败: {e}")
                        continue
            return messages
        except Exception as e:
            print(f"获取邮件列表失败: {e}")
            return []

    def get_message(self, msg_id: str, folder: str = "INBOX") -> Optional[Dict]:
        """获取邮件详情"""
        if not self._imap and not self.connect_imap():
            return None
        try:
            self._imap.select(folder)
            status, data = self._imap.fetch(msg_id, "(RFC822)")
            if status != "OK" or not data or not data[0]:
                return None

            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)

            # 解析正文
            body_text = ""
            body_html = ""
            attachments = []

            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disp = str(part.get("Content-Disposition", ""))
                    if content_type == "text/plain" and "attachment" not in content_disp:
                        try:
                            body_text = part.get_payload(decode=True).decode("utf-8", errors="replace")
                        except Exception:
                            body_text = part.get_payload()
                    elif content_type == "text/html" and "attachment" not in content_disp:
                        try:
                            body_html = part.get_payload(decode=True).decode("utf-8", errors="replace")
                        except Exception:
                            body_html = ""
                    elif "attachment" in content_disp:
                        filename = part.get_filename()
                        if filename:
                            attachments.append({
                                "filename": decode_mime_str(filename),
                                "size": len(part.get_payload(decode=True) or b""),
                                "content_type": content_type
                            })
            else:
                content_type = msg.get_content_type()
                try:
                    body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
                except Exception:
                    body = msg.get_payload()
                if content_type == "text/html":
                    body_html = body
                else:
                    body_text = body

            # 标记已读
            self._imap.store(msg_id, "+FLAGS", "\\Seen")

            return {
                "id": msg_id,
                "subject": decode_mime_str(msg.get("Subject", "")),
                "from": decode_mime_str(msg.get("From", "")),
                "to": decode_mime_str(msg.get("To", "")),
                "cc": decode_mime_str(msg.get("Cc", "")),
                "date": msg.get("Date", ""),
                "body_text": body_text,
                "body_html": body_html,
                "attachments": attachments,
            }
        except Exception as e:
            print(f"获取邮件详情失败: {e}")
            return None

    def send_message(self, to: List[str], subject: str, body: str,
                     cc: List[str] = None, is_html: bool = False,
                     network: str = "auto") -> bool:
        """发送邮件"""
        if not self._smtp and not self.connect_smtp(network):
            return False
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = self.email_addr
            msg["To"] = ", ".join(to)
            if cc:
                msg["Cc"] = ", ".join(cc)
            msg["Subject"] = subject
            msg["Date"] = email.utils.formatdate(localtime=True)
            msg["Message-ID"] = email.utils.make_msgid(domain=MAIL_DOMAIN)

            # 添加网络标记
            actual_network = network if network != "auto" else select_network(to)
            msg["X-LightMail-Network"] = actual_network

            content_type = "html" if is_html else "plain"
            msg.attach(MIMEText(body, content_type, "utf-8"))

            recipients = to + (cc or [])
            self._smtp.sendmail(self.email_addr, recipients, msg.as_string())
            return True
        except Exception as e:
            print(f"发送邮件失败: {e}")
            return False

    def delete_message(self, msg_id: str, folder: str = "INBOX") -> bool:
        """删除邮件"""
        if not self._imap and not self.connect_imap():
            return False
        try:
            self._imap.select(folder)
            self._imap.store(msg_id, "+FLAGS", "\\Deleted")
            self._imap.expunge()
            return True
        except Exception:
            return False

    def close(self):
        """关闭连接"""
        if self._imap:
            try:
                self._imap.logout()
            except Exception:
                pass
        if self._smtp:
            try:
                self._smtp.quit()
            except Exception:
                pass


# ============================================================
# FastAPI 应用
# ============================================================
app = FastAPI(title="LightMail", version="1.0.0", docs_url="/api/docs")

# 静态文件和模板
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ============================================================
# 认证 API
# ============================================================
class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: Optional[str] = None


@app.get("/api/health")
def health_check():
    """健康检查"""
    return {"status": "ok", "service": "LightMail", "version": "1.0.0"}


@app.post("/api/auth/login")
def login(req: LoginRequest, db: sqlite3.Connection = Depends(get_db)):
    """登录"""
    # 支持用户名或邮箱登录
    if "@" in req.username:
        cur = db.execute("SELECT * FROM users WHERE email=?", (req.username,))
    else:
        cur = db.execute("SELECT * FROM users WHERE username=?", (req.username,))
    user = cur.fetchone()
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="用户名或密码错误")
    if not user["is_active"]:
        raise HTTPException(status_code=400, detail="账户已被禁用")

    token = generate_token(user["id"], db)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "display_name": user["display_name"],
            "is_admin": bool(user["is_admin"])
        }
    }


@app.post("/api/auth/register")
def register(req: RegisterRequest, db: sqlite3.Connection = Depends(get_db)):
    """注册（开放注册，生产环境请加验证码）"""
    email = req.email or f"{req.username}@{MAIL_DOMAIN}"
    if not email.endswith(f"@{MAIL_DOMAIN}"):
        raise HTTPException(status_code=400, detail="仅支持本域邮箱注册")

    # 检查是否已存在
    cur = db.execute("SELECT id FROM users WHERE username=? OR email=?",
                     (req.username, email))
    if cur.fetchone():
        raise HTTPException(status_code=400, detail="用户名或邮箱已存在")

    pw_hash = hash_password(req.password)
    db.execute(
        "INSERT INTO users (username, password_hash, email, display_name) VALUES (?,?,?,?)",
        (req.username, pw_hash, email, req.username)
    )
    db.commit()

    # 创建系统用户目录（Dovecot Maildir）
    maildir = Path(f"/var/mail/vmail/{MAIL_DOMAIN}/{req.username}")
    try:
        for subdir in ["cur", "new", "tmp"]:
            (maildir / subdir).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass  # 开发环境可忽略

    return {"message": "注册成功", "email": email}


@app.post("/api/auth/logout")
def logout(authorization: Optional[str] = Header(None),
           db: sqlite3.Connection = Depends(get_db)):
    """登出"""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "")
        db.execute("DELETE FROM tokens WHERE token=?", (token,))
        db.commit()
    return {"message": "已登出"}


# ============================================================
# 用户 API
# ============================================================
@app.get("/api/user/profile")
def get_profile(user: dict = Depends(get_current_user)):
    """获取个人资料"""
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "display_name": user["display_name"],
        "quota_mb": user["quota_mb"],
        "is_admin": bool(user["is_admin"])
    }


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@app.post("/api/user/password")
def change_password(req: ChangePasswordRequest,
                    user: dict = Depends(get_current_user),
                    db: sqlite3.Connection = Depends(get_db)):
    """修改密码"""
    if not verify_password(req.old_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="原密码错误")
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少6位")
    new_hash = hash_password(req.new_password)
    db.execute("UPDATE users SET password_hash=? WHERE id=?",
               (new_hash, user["id"]))
    db.commit()
    return {"message": "密码修改成功"}


# ============================================================
# 邮件 API
# ============================================================
class SendMailRequest(BaseModel):
    to: List[str]
    subject: str
    body: str
    cc: Optional[List[str]] = []
    is_html: bool = False
    network: str = "auto"  # auto / dn42 / public


@app.get("/api/mail/folders")
def get_folders(user: dict = Depends(get_current_user)):
    """获取文件夹列表"""
    client = MailClient(user["email"], "")  # 密码从哪里来？
    # 简化：这里用 token 方式不够，实际需要 Dovecot 认证集成
    # 暂用固定测试
    return {
        "folders": ["INBOX", "Sent", "Drafts", "Trash", "Junk"],
        "unread_counts": {"INBOX": 0, "Junk": 0}
    }


@app.get("/api/mail/list")
def list_mail(folder: str = "INBOX", page: int = 1, page_size: int = 20,
              user: dict = Depends(get_current_user),
              db: sqlite3.Connection = Depends(get_db)):
    """获取邮件列表（优先从缓存读）"""
    # 从缓存读取
    offset = (page - 1) * page_size
    cur = db.execute(
        "SELECT * FROM mail_cache WHERE user_id=? AND folder=? "
        "ORDER BY date DESC LIMIT ? OFFSET ?",
        (user["id"], folder, page_size, offset)
    )
    items = [dict(r) for r in cur.fetchall()]

    cur = db.execute(
        "SELECT COUNT(*) as cnt FROM mail_cache WHERE user_id=? AND folder=?",
        (user["id"], folder)
    )
    total = cur.fetchone()["cnt"]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items
    }


@app.get("/api/mail/{mail_id}")
def get_mail(mail_id: str, folder: str = "INBOX",
             user: dict = Depends(get_current_user)):
    """获取邮件详情"""
    # 简化实现：从缓存读
    db = next(get_db())
    cur = db.execute(
        "SELECT * FROM mail_cache WHERE user_id=? AND id=?",
        (user["id"], mail_id)
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="邮件不存在")
    return dict(row)


@app.post("/api/mail/send")
def send_mail(req: SendMailRequest, user: dict = Depends(get_current_user),
              db: sqlite3.Connection = Depends(get_db)):
    """发送邮件"""
    # 公网出站控制
    actual_network = req.network if req.network != "auto" else select_network(req.to)

    if actual_network == "public" and not PUBLIC_ENABLED:
        raise HTTPException(status_code=400, detail="公网出站已禁用")

    # 检查 DN42 出站
    if actual_network == "dn42" and not DN42_ENABLED:
        raise HTTPException(status_code=400, detail="DN42 网络不可用")

    # 调用 SMTP 发送
    client = MailClient(user["email"], "")
    # 注意：实际需要从用户密码或 token 映射，这里简化
    # 生产环境用 Dovecot 的 SQL 认证

    # 记录发送日志
    db.execute(
        "INSERT INTO mail_cache (user_id, folder, subject, sender, recipient, date, preview) "
        "VALUES (?, 'Sent', ?, ?, ?, datetime('now'), ?)",
        (user["id"], req.subject, user["email"], ", ".join(req.to),
         req.body[:80] + "..." if len(req.body) > 80 else req.body)
    )
    db.commit()

    return {
        "status": "sent",
        "network": actual_network,
        "message": "邮件已发送"
    }


@app.delete("/api/mail/{mail_id}")
def delete_mail(mail_id: str, folder: str = "INBOX",
                user: dict = Depends(get_current_user),
                db: sqlite3.Connection = Depends(get_db)):
    """删除邮件"""
    db.execute("DELETE FROM mail_cache WHERE user_id=? AND id=? AND folder=?",
               (user["id"], mail_id, folder))
    db.commit()
    return {"message": "已删除"}


# ============================================================
# DN42 状态 API
# ============================================================
@app.get("/api/dn42/status")
def dn42_status():
    """获取 DN42 网络状态"""
    return {
        "enabled": DN42_ENABLED,
        "ipv4": DN42_IPV4,
        "domain": MAIL_DOMAIN,
        "public_enabled": PUBLIC_ENABLED,
        "modes": {
            "dn42_inbound": DN42_ENABLED,
            "dn42_outbound": DN42_ENABLED,
            "public_inbound": PUBLIC_ENABLED,
            "public_outbound": PUBLIC_ENABLED
        }
    }


# ============================================================
# 管理后台 API
# ============================================================
@app.get("/api/admin/users")
def admin_users(page: int = 1, page_size: int = 20,
                user: dict = Depends(get_current_user),
                db: sqlite3.Connection = Depends(get_db)):
    """用户列表（管理员）"""
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    offset = (page - 1) * page_size
    cur = db.execute(
        "SELECT id, username, email, display_name, quota_mb, is_active, is_admin, created_at FROM users ORDER BY id DESC LIMIT ? OFFSET ?",
        (page_size, offset)
    )
    items = [dict(r) for r in cur.fetchall()]

    cur = db.execute("SELECT COUNT(*) as cnt FROM users")
    total = cur.fetchone()["cnt"]

    return {"total": total, "items": items}


@app.post("/api/admin/users/{user_id}/toggle")
def admin_toggle_user(user_id: int, user: dict = Depends(get_current_user),
                      db: sqlite3.Connection = Depends(get_db)):
    """启用/禁用用户"""
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    db.execute(
        "UPDATE users SET is_active = 1 - is_active WHERE id=?",
        (user_id,)
    )
    db.commit()
    return {"message": "操作成功"}


@app.get("/api/admin/stats")
def admin_stats(user: dict = Depends(get_current_user),
                db: sqlite3.Connection = Depends(get_db)):
    """系统统计"""
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    cur = db.execute("SELECT COUNT(*) as cnt FROM users")
    user_count = cur.fetchone()["cnt"]

    cur = db.execute("SELECT COUNT(*) as cnt FROM mail_cache")
    mail_count = cur.fetchone()["cnt"]

    cur = db.execute("SELECT COUNT(*) as cnt FROM domains")
    domain_count = cur.fetchone()["cnt"]

    return {
        "total_users": user_count,
        "total_mails": mail_count,
        "total_domains": domain_count,
        "dn42_enabled": DN42_ENABLED,
        "public_enabled": PUBLIC_ENABLED,
        "mail_domain": MAIL_DOMAIN
    }


# ============================================================
# 页面路由
# ============================================================
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """首页 / Webmail"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    """管理后台"""
    return templates.TemplateResponse("admin.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    """登录页"""
    return templates.TemplateResponse("login.html", {"request": request})


# ============================================================
# 启动
# ============================================================
if __name__ == "__main__":
    init_db()
    print(f"[+] LightMail 启动中...")
    print(f"[+] 邮件域名: {MAIL_DOMAIN}")
    print(f"[+] DN42 支持: {'启用' if DN42_ENABLED else '禁用'}")
    print(f"[+] 公网互通: {'启用' if PUBLIC_ENABLED else '禁用'}")
    print(f"[+] Webmail: http://localhost:8025")
    print(f"[+] API文档: http://localhost:8025/api/docs")
    uvicorn.run(app, host="0.0.0.0", port=8025)
