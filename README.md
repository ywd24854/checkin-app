# 比赛打卡记录 · Web 版

每日照片打卡 + 任务提交 + 管理后台汇总导出。单机可跑，不依赖任何第三方服务。

## 快速开始

1. 双击根目录 **`start.bat`**（唯一入口；`run.bat` 是它的兼容别名）
   - 首次会检查并自动安装依赖，然后选一个空闲端口（默认 8000）
   - 需要中文菜单（装依赖 / 备份 / 查地址 / 自检）：`start.bat menu`
   - 指定端口：`start.bat 8080`；只要本机可访问：把 `config.bat` 里的 `HOST` 设成 `127.0.0.1`
2. 浏览器打开窗口里打印的地址（本机 `http://127.0.0.1:8000`，队员手机用同一 WiFi 下的局域网地址）
3. 队员：**只填姓名**就能进（首次填自动建档），不需要手机号、不需要密码；进过一次后本机会记住名字，下次只点一下「继续打卡」
4. 管理员：`admin` / `123456`（首次登录后请立刻改密码）；可在后台预置名单和队伍

依赖：Python 3.10+、`fastapi`、`uvicorn`、`python-multipart`、`pillow`、`openpyxl`（见 `requirements.txt`）

部署到 Windows 服务器的完整步骤（防火墙、开机自启、备份迁移、离线装依赖、常见问题）见 **[DEPLOY-Windows.md](DEPLOY-Windows.md)**。

## 目录

```
start.bat            启动入口（纯 ASCII：查 Python → 装依赖 → 调 launcher.py）
launcher.py          中文菜单 / 端口检测 / 局域网地址 / 日志 / 一键备份
config.bat.example   可选配置（端口、绑定地址、初始密码、HTTPS Cookie）
app/main.py          全部接口（FastAPI + SQLite）
app/static/          login.html · index.html · admin.html · style.css
data/                运行时生成：checkin.db、uploads/、secret.key、logs/
backups/             start.bat menu → 3 生成的备份包
```

## 功能

| 需求 | 实现 |
|---|---|
| 每天照片打卡 | 队员上传照片 + 填当日任务；照片自动压到长边 1600 并盖「日期 + 姓名」水印 |
| 一天一条 | `checkins` 表 `UNIQUE(user_id, day)`，当天再提交是覆盖更新，不会出现双份 |
| 注册只要姓名 | 首页填姓名即建档并进入；姓名唯一，同名自动识别为同一人 |
| 记住登录 | 队员会话 Cookie 30 天（管理员 7 天）；姓名另存本机，Cookie 过期也能一键「继续打卡」；顶栏「换人」清记忆并退出 |
| 管理员加人 | 后台新增成员（只要姓名，队伍/手机号可选）、CSV 批量导入（`姓名[,队伍][,手机号]`） |
| 提交当日完成任务 | 打卡表单里的「今日完成的任务」文本 + 备注 |
| 后台导出汇总 | Excel（两个 sheet：打卡明细 + 人统计含出勤率）或 CSV（UTF-8 BOM，Excel 直开不乱码） |
| 其他 | 成员停用/启用、**彻底移除成员**（连带删除其打卡记录与照片）、按姓名/队伍/手机号搜索、按日期区间+关键词查记录、今日未打卡名单 |

## 管理员账号 / 密码怎么改

两种方式：

**1. 在后台改（推荐）**：登录后台 → 右上角「**账号设置**」→ 输入当前密码 → 改账号和/或新密码 → 保存。
- 账号限 2~32 位字母、数字、下划线、点、短横线；不能与已有队员姓名重复（否则队员进不来）
- 新密码留空表示只改账号；改完请用新账号重新登录
- 队员入口只填姓名，完全不受影响

**2. 首次部署时用环境变量定初始密码**（只在数据库里还没有管理员时生效）：

```bat
set CHECKIN_ADMIN_PASSWORD=你的强密码
run.bat
```

忘记密码怎么办：停掉服务，用 SQLite 工具删掉 `users` 表里 `role='admin'` 那条，重启后会用默认 `admin / 123456`（或环境变量里的密码）重新建一个。

## 两个入口是严格分开的

| 入口 | 认证方式 | 失败行为 |
|---|---|---|
| 队员（首页） | 只填姓名，姓名唯一 | 姓名被停用则拒绝 |
| 管理员（登录页切过去） | 账号 + 密码 | 账号不存在 / 密码错误都直接拒绝，**不会**顺带建队员账号 |

## 目录

```
app/
  main.py              # FastAPI 全部接口（约 480 行，单文件）
  static/
    login.html         # 登录 / 注册
    index.html         # 队员：今日打卡 + 我的记录
    admin.html         # 管理员：概览 / 成员 / 记录 / 导出
    style.css
data/                  # 运行时生成（数据库、上传照片、secret.key），不进 git
  checkin.db
  uploads/YYYYMM/*.jpg
run.bat                # 启动脚本
```

## 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/enter` | 队员入口：`name`（必填）、`team`（可选）→ 建档并登录 |
| POST | `/api/login` | 管理员登录（`identity` + `password`，严格校验，不建档） |
| POST | `/api/admin/account` | 改管理员自己的账号/密码（需 `current_password`） |
| POST | `/api/logout` | 退出（HMAC 签名 Cookie，7 天） |
| GET | `/api/me` | 当前用户 |
| POST | `/api/checkin` | 上传照片 + 任务（multipart，当天 upsert） |
| GET | `/api/my/today` · `/api/my/checkins?month=` | 今日状态 / 我的记录 |
| GET/POST | `/api/admin/members` | 成员列表 / 新增（只要姓名） |
| POST | `/api/admin/members/import` | CSV 导入（`姓名[,队伍][,手机号]`） |
| POST | `/api/admin/members/{id}/toggle` | 停用 / 启用 |
| POST | `/api/admin/members/{id}/delete` | 彻底移除成员（连同打卡记录与照片；管理员账号不可移除） |
| GET | `/api/admin/checkins?start&end&uid&q` | 记录查询 |
| GET | `/api/admin/stats?day=` | 今日应打卡/已打卡/未打卡名单 |
| GET | `/api/admin/export?start&end&fmt=xlsx\|csv` | 导出汇总 |

## 部署到服务器

```bash
# 依赖
pip install fastapi uvicorn python-multipart pillow openpyxl
# 起服务（生产建议多 worker + 前置 Nginx）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
```

- 反向代理记得：`client_max_body_size 20m`（照片上传）、转发 Cookie
- `data/` 目录需要可写；备份 = 拷 `data/` 整个目录
- 首次上线必须做：改 `admin` 密码、确认 `data/secret.key` 不被提交到仓库

## 已知边界

- **姓名即身份**：同名会被当成同一个人（姓名唯一）；内部比赛场景够用，若要防冒名可在 `_enter_by_name()` 里加口令或改成「姓名 + 队伍」
- 单进程 SQLite 够用（几百人规模）；并发写入很高再换 PostgreSQL
- 照片存本地磁盘；要上对象存储就改 `save_photo()` 的落盘部分
- 没有短信/微信登录、没有打卡提醒，需要可加定时任务
- 比赛用的时区按服务器本地时区算「今天」，跨时区部署要固定 `TZ=Asia/Shanghai`
