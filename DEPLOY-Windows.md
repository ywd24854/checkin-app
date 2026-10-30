# Windows 原生部署说明

目标：把压缩包解压到 Windows 机器上，双击 `start.bat` 就能用。不装 Docker、不装数据库、不需要 Node。

## 一、压缩包里有什么

```
checkin-app/
├── start.bat                ← 双击这个启动（唯一入口）
├── run.bat                  ← 兼容旧叫法，效果同 start.bat
├── launcher.py              ← 中文菜单 / 端口检测 / 日志 / 备份都在这里
├── config.bat.example       ← 需要改端口、初始密码时，复制成 config.bat
├── requirements.txt         ← 5 个 Python 依赖
├── app/
│   ├── main.py              ← 全部后端接口
│   └── static/              ← 三个页面 + 样式（原生 JS，无构建步骤）
├── README.md                ← 功能与接口说明
└── DEPLOY-Windows.md        ← 本文件
```

**不含** `data/`（数据库、照片、`secret.key`）——服务器上第一次启动会自动生成。

## 二、目标机要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10 / 11 / Server 2016 及以上（64 位） |
| Python | 3.10 及以上（建议 3.11~3.13） |
| 磁盘 | 程序 1 MB + 照片（每张约 100~300 KB，一天一百人约 20 MB/天） |
| 网络 | 首次装依赖需要联网；之后纯内网即可 |
| 权限 | 建议放在非系统盘（如 `D:\checkin-app`），不要在 `C:\Program Files` 下（权限问题） |

## 三、部署步骤

### 1. 装 Python（没装过才需要）

去 <https://www.python.org/downloads/windows/> 下 3.12 或 3.13 的 **Windows installer (64-bit)**。

安装时**务必勾选** `Add python.exe to PATH`，其余默认。

验证：按 `Win + R` 输入 `cmd` 回车，执行

```bat
python --version
```

能打印 `Python 3.x.x` 就行。

### 2. 解压

把 `checkin-app-*.zip` 解压到例如 `D:\checkin-app`。

> 路径里最好不要有中文和空格，虽然大多数情况没事，但能避免一堆奇怪问题。

### 3. 双击启动

双击 **`start.bat`**。第一次会：

1. 检查依赖 → 缺了就自动 `pip install`（几分钟，看网速）
2. 选一个空闲端口（默认 8000，被占用会自动往后找）
3. 打印访问地址，并自动打开浏览器

窗口里会显示：

```
  本机访问：http://127.0.0.1:8000
  局域网  ：http://192.168.1.23:8000   ← 队员手机连同一个 WiFi 用这个
```

**首次访问 Windows 防火墙可能弹窗**，勾选「专用网络」并点「允许访问」，否则同网段的手机打不开。

### 4. 登录后台改密码

浏览器打开 `http://127.0.0.1:8000` → 「管理员」→ 账号 `admin`、密码 `123456` → 进后台右上角「**账号设置**」立刻改掉。

### 5. 录入队员

后台「成员管理」：

- 单个添加：只要姓名，队伍可选
- 批量导入：上传 CSV（列：`姓名` 或 `姓名,队伍`），Excel 另存为 CSV 即可

队员用手机打开局域网地址，填姓名就能打卡，不用账号密码。

## 四、可选配置：config.bat

需要改端口、绑定地址、初始密码时：

```bat
copy config.bat.example config.bat
notepad config.bat
```

把需要的行的 `rem` 去掉：

```bat
set "PORT=8080"
set "HOST=0.0.0.0"
set "CHECKIN_ADMIN_PASSWORD=换成你的密码"
```

| 变量 | 作用 | 默认 |
|---|---|---|
| `PORT` | 端口 | 8000 |
| `HOST` | `0.0.0.0` 局域网可访问；`127.0.0.1` 只有本机能访问 | 0.0.0.0 |
| `CHECKIN_ADMIN_PASSWORD` | **只在数据库里还没有管理员时**用于创建初始密码 | 123456 |
| `CHECKIN_COOKIE_SECURE` | 走 HTTPS 时设 1，登录 Cookie 只在 HTTPS 下发送 | 未设 |

> 密码已经在后台改过的话，`CHECKIN_ADMIN_PASSWORD` 不会覆盖它——想重置就走文末「忘记密码」。

## 五、开机自启（服务器常驻）

### 方式 A：任务计划程序（推荐，不需要登录桌面）

以**管理员身份**打开 cmd，执行（路径换成你的）：

```bat
schtasks /create /tn "CheckinApp" /tr "\"D:\checkin-app\start.bat\"" /sc onstart /ru SYSTEM /rl highest /f
```

立即测试一次：

```bat
schtasks /run /tn "CheckinApp"
```

取消自启：

```bat
schtasks /delete /tn "CheckinApp" /f
```

> 用 SYSTEM 账户跑时是隐藏窗口，看不到控制台；日志在 `data\logs\server-*.log`。
> 想开机自动打开浏览器的话用方式 B。

### 方式 B：启动文件夹（会弹出黑窗口，方便看日志）

`Win + R` 输入 `shell:startup` 回车，把 `start.bat` 的**快捷方式**拖进去。

## 六、数据、备份与迁移

| 路径 | 内容 | 要不要备份 |
|---|---|---|
| `data\checkin.db` | 成员 + 打卡记录（SQLite） | **要** |
| `data\checkin.db-wal` / `-shm` | SQLite WAL 文件，运行时存在 | 一起备份 |
| `data\uploads\YYYYMM\*.jpg` | 打卡照片（已压到长边 1600 并带水印） | **要** |
| `data\secret.key` | 会话签名密钥 | 要，且**不要外传** |
| `data\logs\` | 运行日志 | 不用 |
| `backups\` | 一键备份产物 | —— |

**一键备份**：`start.bat menu` → 选 `3`，会在 `backups\` 生成 `checkin-data-时间戳.zip`。

**手动备份**：直接复制整个 `data\` 目录。

**迁移到另一台机器**：新机器解压程序 → 把旧的 `data\` 整个拷进新目录 → 双击 `start.bat`。数据、照片、登录态全都在。

**升级程序**：只替换 `app\` 目录和 `launcher.py`，**不要动 `data\`**。库结构如果有变化，程序启动时会自动迁移。

## 七、局域网 / 公网访问

- **局域网**（推荐，比赛现场最常见）：队员手机连同一个 WiFi，访问 `http://服务器IP:8000`。服务器 IP 用 `ipconfig` 查，建议在路由器上给它固定 IP。
- **公网**：不要把 8000 直接映射到公网。最低成本做法是 IIS 或 Nginx 做反向代理 + HTTPS，然后设 `CHECKIN_COOKIE_SECURE=1`。
  反向代理要注意：`client_max_body_size` 至少 20m（手机照片），并透传 `X-Forwarded-For`。

## 八、常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 双击 start.bat 闪一下就没了 | Python 没装或没进 PATH。按第三节第 1 步重装，勾选 Add to PATH |
| 提示 `pip install failed` | 没网或被墙。挂代理，或者按下面「离线安装」 |
| 提示端口被占用 | 换端口：`start.bat 8080`；或先关掉占 8000 的程序（`netstat -ano | findstr :8000`） |
| 手机打不开，本机能开 | ① `HOST` 是不是 0.0.0.0；② 防火墙没放行；③ 手机和电脑不在同一个网段 |
| 页面打开是旧样子 | 已给 HTML 加了 `no-store`，正常刷新即可；极旧情况 Ctrl+F5 |
| 上传照片失败 | 反向代理 body 太小；或磁盘满了 |
| 后台导出的 Excel 打不开 | 文件是 .xlsx，用 Excel 2010+ 或 WPS 打开 |
| 时间/日期不对 | 「今天」取服务器本地时区，确认系统时区是北京时间 |
| 忘记管理员密码 | 停服务 → 删掉 `data\checkin.db` 里 `role='admin'` 那一条 → 重启（会按默认/环境变量重建）。稳妥做法：先备份 `data\` |

### 离线安装依赖（服务器无外网）

在有网的机器上（同版本 Python）：

```bat
pip download -r requirements.txt -d wheels
```

把 `wheels` 文件夹一起拷到服务器，在服务器目录执行：

```bat
python -m pip install --no-index --find-links=wheels -r requirements.txt
```

装完再双击 `start.bat`。

## 九、上线前检查清单

- [ ] 后台「账号设置」把 `admin` 密码改掉
- [ ] 成员名单和队伍录好（或让队员自己填）
- [ ] 手机连同一 WiFi 试一次完整打卡（含照片）
- [ ] 后台「导出汇总」下载一次 Excel，确认照片地址列可用
- [ ] 设了开机自启（服务器重启后能自动跑）
- [ ] `data\` 有备份，且 `secret.key` 没被发到群里
