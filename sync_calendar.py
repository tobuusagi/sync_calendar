#!/usr/bin/env python3
import os
import base64
import argparse
import requests
import datetime
import json
import sys
import time
import pickle
from typing import List, Dict, Optional
from icalendar import Calendar

# Google Auth 相关库
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# 尝试从 .env 文件加载环境变量
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 配置信息 - 从 GitHub Secrets 读取
API_KEY = os.environ.get("ZECTRIX_API_KEY", "zt_1b115e3e75bb2926e6078998f734e092")
API_BASE = "https://cloud.zectrix.com/open/v1"
DEVICE_IDS = [
    "DC:B4:D9:19:1C:F0", 
    "AC:A7:04:E9:5F:0C"
]
EXPIRE_HOURS = 1

# Google CalDAV 配置
CALDAV_URL = "https://apidata.googleusercontent.com/caldav/v2/{email}/user"
CALDAV_USER = "cj263130@gmail.com"
CALENDAR_PREFIX = "[日历]"

# OAuth2 Scopes
SCOPES = ['https://www.googleapis.com/auth/calendar']


def decode_secret(secret_name: str) -> str:
    """从环境变量读取 base64 编码的 Secret 并解码"""
    encoded = os.environ.get(secret_name)
    if not encoded:
        print(f"警告: 未找到环境变量 {secret_name}")
        return None
    return base64.b64decode(encoded).decode('utf-8')


class CalendarSyncer:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.headers = {
            "X-API-Key": API_KEY,
            "Content-Type": "application/json"
        }
        self.existing_todos = []
        self._uid_map: Dict[str, Dict] = {}
        self.max_retries = 3

    def setup_google_credentials(self):
        """从 GitHub Secrets 设置 Google 凭证文件"""
        # 解码 credentials.json
        credentials_json = decode_secret("CREDENTIALS_JSON")
        if credentials_json:
            with open('credentials.json', 'w') as f:
                f.write(credentials_json)
            print("✓ credentials.json 已从 Secret 写入")
        else:
            print("错误: CREDENTIALS_JSON 环境变量未设置")
            return False

        # 解码 token.pickle
        token_pickle = decode_secret("TOKEN_PICKLE")
        if token_pickle:
            with open('token.pickle', 'wb') as f:
                f.write(token_pickle.encode('latin-1') if isinstance(token_pickle, str) else token_pickle)
            print("✓ token.pickle 已从 Secret 写入")
        else:
            print("错误: TOKEN_PICKLE 环境变量未设置")
            return False

        return True

    def get_google_auth_token(self):
        """获取或刷新 Google OAuth2 Token"""
        creds = None
        if os.path.exists('token.pickle'):
            with open('token.pickle', 'rb') as token:
                creds = pickle.load(token)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists('credentials.json'):
                    print("错误: 找不到 credentials.json 文件。")
                    return None
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(open_browser=False)
            
            # 刷新后的 token 保存回环境变量（GitHub Actions 中不会持久化，但本地调试可用）
            with open('token.pickle', 'wb') as token:
                pickle.dump(creds, token)
        
        return creds.token

    def retry_with_backoff(self, func, *args, **kwargs):
        for attempt in range(self.max_retries):
            try:
                result = func(*args, **kwargs)
                if result is False:
                    delay = 2 ** attempt
                    print(f"  重试 {attempt + 1}/{self.max_retries}, 等待 {delay} 秒...")
                    time.sleep(delay)
                    continue
                return result
            except Exception as e:
                delay = 2 ** attempt
                print(f"  尝试 {attempt + 1}/{self.max_retries} 失败: {e}, 等待 {delay} 秒...")
                time.sleep(delay)
        return None

    def get_existing_todos(self, device_id: str) -> List[Dict]:
        def _get():
            url = f"{API_BASE}/todos"
            params = {"status": 0, "deviceId": device_id}
            resp = requests.get(url, headers=self.headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == 0:
                self.existing_todos = data.get("data", [])
                self._uid_map = {
                    uid: todo
                    for todo in self.existing_todos
                    if (uid := self.extract_uid_from_description(todo.get("description", "")))
                }
                return self.existing_todos
            return False
        result = self.retry_with_backoff(_get)
        return result if result is not None else []

    def is_expired(self, dueDate: str, dueTime: str) -> bool:
        try:
            due_datetime = datetime.datetime.strptime(f"{dueDate} {dueTime}", "%Y-%m-%d %H:%M")
            now = datetime.datetime.now()
            return (now - due_datetime).total_seconds() >= EXPIRE_HOURS * 3600
        except:
            return False

    def _calendar_todos(self):
        for todo in self.existing_todos:
            if todo.get("status", 1) == 0 and todo.get("title", "").startswith(CALENDAR_PREFIX):
                yield todo

    def complete_expired_calendar_todos(self):
        for todo in self._calendar_todos():
            if self.is_expired(todo.get("dueDate", ""), todo.get("dueTime", "")):
                self.complete_todo(todo.get("id"))

    def complete_todo(self, todo_id: int) -> bool:
        if self.dry_run: return True
        def _complete():
            resp = requests.put(f"{API_BASE}/todos/{todo_id}/complete", headers=self.headers, timeout=10)
            return resp.json().get("code") == 0
        return self.retry_with_backoff(_complete) or False

    def delete_todo(self, todo_id: int) -> bool:
        if self.dry_run: return True
        def _delete():
            resp = requests.delete(f"{API_BASE}/todos/{todo_id}", headers=self.headers, timeout=10)
            return resp.json().get("code") == 0
        return self.retry_with_backoff(_delete) or False

    def fetch_aliyun_calendar_events(self) -> List[Dict]:
        token = self.get_google_auth_token()
        if not token:
            return []

        def _fetch():
            import caldav
            client = caldav.DAVClient(
                url=CALDAV_URL.format(email=CALDAV_USER),
                headers={"Authorization": f"Bearer {token}"}
            )

            principal = client.principal()
            calendars = principal.calendars()
            events = []
            now = datetime.datetime.now().astimezone()
            start_search = now - datetime.timedelta(minutes=10)
            target_end = now.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=2)

            for calendar in calendars:
                events_found = calendar.date_search(start=start_search, end=target_end)
                for event in events_found:
                    events.extend(self.parse_caldav_event(event))
            return events

        result = self.retry_with_backoff(_fetch)
        return result if result is not None else []

    def parse_caldav_event(self, event) -> List[Dict]:
        events = []
        try:
            cal = Calendar.from_ical(event.data)
            now = datetime.datetime.now().astimezone()
            target_end = now.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=2)

            for component in cal.walk():
                if component.name == "VEVENT":
                    summary = str(component.get('SUMMARY', ''))
                    dtstart = component.get('DTSTART')
                    uid = str(component.get('UID', ''))
                    if not summary or not dtstart: continue
                    
                    dt = dtstart.dt
                    if isinstance(dt, datetime.datetime):
                        dt = dt.astimezone()
                    else:
                        dt = datetime.datetime.combine(dt, datetime.time(9, 0)).astimezone()

                    if now <= dt <= target_end:
                        events.append({
                            "uid": uid,
                            "title": summary.strip(),
                            "dueDate": dt.strftime("%Y-%m-%d"),
                            "dueTime": dt.strftime("%H:%M")
                        })
            return events
        except:
            return []

    def create_todo(self, device_id: str, uid: str, title: str, dueDate: str, dueTime: str) -> bool:
        if self.dry_run: return True
        def _create():
            data = {
                "title": f"{CALENDAR_PREFIX} {title}".strip(),
                "description": f"从邮箱日历同步\nUID: {uid}",
                "dueDate": dueDate,
                "dueTime": dueTime,
                "repeatType": "none",
                "priority": 1,
                "deviceId": device_id
            }
            resp = requests.post(f"{API_BASE}/todos", headers=self.headers, json=data, timeout=10)
            return resp.json().get("code") == 0
        return self.retry_with_backoff(_create) or False

    def update_todo(self, todo_id: int, uid: str, title: str, dueDate: str, dueTime: str) -> bool:
        if self.dry_run: return True
        def _update():
            data = {
                "title": f"{CALENDAR_PREFIX} {title}".strip(),
                "description": f"从邮箱日历同步\nUID: {uid}",
                "dueDate": dueDate, "dueTime": dueTime
            }
            resp = requests.put(f"{API_BASE}/todos/{todo_id}", headers=self.headers, json=data, timeout=10)
            return resp.json().get("code") == 0
        return self.retry_with_backoff(_update) or False

    def extract_uid_from_description(self, description: str) -> str:
        if not description: return ""
        for line in description.split('\n'):
            if line.strip().startswith('UID:'):
                return line.strip()[4:].strip()
        return ""

    def sync_new_events(self, device_id: str, events: List[Dict]):
        current_uids = {e["uid"] for e in events if e.get("uid")}
        for event in events:
            uid = event["uid"]
            existing = self._uid_map.get(uid)
            if not existing:
                self.create_todo(device_id, uid, event["title"], event["dueDate"], event["dueTime"])
            else:
                if (existing.get("title", "").replace(CALENDAR_PREFIX, "").strip() != event["title"] or
                    existing.get("dueDate") != event["dueDate"] or existing.get("dueTime") != event["dueTime"]):
                    self.update_todo(existing["id"], uid, event["title"], event["dueDate"], event["dueTime"])

        for todo in self._calendar_todos():
            uid = self.extract_uid_from_description(todo.get("description", ""))
            if uid and uid not in current_uids:
                self.delete_todo(todo.get("id"))

    def run(self):
        print(f"开始同步: {datetime.datetime.now()}")
        
        # 设置 Google 凭证
        if not self.setup_google_credentials():
            print("无法设置 Google 凭证，退出")
            return
        
        # 获取日历事件
        events = self.fetch_aliyun_calendar_events()
        if not events:
            print("没有获取到有效的日历事件或获取失败，跳过本次设备同步。")
            return

        # 同步到各设备
        for device_id in DEVICE_IDS:
            print(f"\n>>>> 正在同步设备: {device_id} <<<<")
            self.get_existing_todos(device_id)
            self.complete_expired_calendar_todos()
            self.sync_new_events(device_id, events)
            
        print("\n所有设备同步完成!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    syncer = CalendarSyncer(dry_run=args.dry_run)
    syncer.run()
