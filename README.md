# Google Calendar → Zectrix Sync

将 Google Calendar 日历事件同步到 Zectrix 待办事项。

## 功能

- 从 Google Calendar 获取未来 2 天内的日历事件
- 同步到指定的 Zectrix 设备
- 自动完成过期的日历事件
- 支持多设备同步

## 工作原理

脚本通过 Google CalDAV API 获取日历事件，然后通过 Zectrix API 创建/更新/删除待办事项。

## 配置

### GitHub Secrets

在仓库 Settings → Secrets and variables → Actions 中添加：

| Secret 名称 | 说明 | 获取方式 |
|-------------|------|----------|
| `CREDENTIALS_JSON` | Google OAuth2 凭证文件 (base64) | `base64 -w 0 credentials.json` |
| `TOKEN_PICKLE` | Google OAuth2 Token 文件 (base64) | `base64 -w 0 token.pickle` |
| `ZECTRIX_API_KEY` | Zectrix API Key | 直接填入 |

### 获取 Google 凭证

1. 访问 [Google Cloud Console](https://console.cloud.google.com/)
2. 创建项目并启用 Calendar API
3. 创建 OAuth2 凭证（桌面应用类型）
4. 下载 credentials.json
5. 首次运行脚本完成授权，生成 token.pickle

```bash
# 本地运行一次完成授权
pip install google-auth google-auth-oauthlib caldav icalendar requests
python sync_calendar.py

# 生成 base64 编码
base64 -w 0 credentials.json  # 复制输出到 CREDENTIALS_JSON Secret
base64 -w 0 token.pickle      # 复制输出到 TOKEN_PICKLE Secret
```

## 手动触发

```bash
# 通过 GitHub CLI
gh workflow run sync.yml

# 通过 API
curl -X POST \
  -H "Authorization: token YOUR_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/repos/OWNER/REPO/actions/workflows/sync.yml/dispatches \
  -d '{"event_type": "cron-trigger"}'
```

## 外部定时触发 (cronjob.org)

1. 访问 https://cronjob.org
2. 创建定时任务
3. URL: `https://api.github.com/repos/OWNER/REPO/actions/workflows/sync.yml/dispatches`
4. Method: POST
5. Headers: `Authorization: token YOUR_GITHUB_TOKEN`, `Accept: application/vnd.github.v3+json`
6. Body: `{"event_type": "cron-trigger"}`
7. 频率: 每 15 分钟

## 设备配置

当前同步到以下设备：
- `DC:B4:D9:19:1C:F0`
- `AC:A7:04:E9:5F:0C`

如需修改，编辑 `sync_calendar.py` 中的 `DEVICE_IDS` 列表。

## 依赖

- requests
- icalendar
- caldav
- google-auth
- google-auth-oauthlib
- python-dotenv
