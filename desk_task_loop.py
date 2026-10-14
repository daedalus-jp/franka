#!/usr/bin/env python3
"""
Franka Desk のタスクを繰り返し実行するスクリプト（展示会用）

- Windows の Python でも WSL2 でも動きます（HTTP 通信のみ・リアルタイム不要）
- 制御権・セルフテスト・関節ロックは公式の Desk API（/api/...）を使用
- タスク開始だけは公式 API に無いため、Desk 画面が内部で使っている通信を使います
  （EXECUTION_PATH を開発者ツールで確認した内容に合わせてください）

使い方:
    pip install requests
    python desk_task_loop.py --status     # 状態の確認だけ（制御権は取らない）
    python desk_task_loop.py --check      # 制御権を取って返すだけ
    python desk_task_loop.py              # ループ開始（Ctrl+C で終了）
"""

import argparse
import base64
import hashlib
import sys
import time
from datetime import datetime

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ===== 設定（ここを書き換える） =====
ROBOT_IP = "192.168.100.49"
USERNAME = "admin"
PASSWORD = "taroworld"
TASK_NAME = "0_start-move"   # タスクの ID（Desk 画面の通信で送られている id= の値。番号_タスク名 の形）

WAIT_AFTER_TASK = 2.0        # 1回開始してから次の開始を試すまでの待ち [秒]
RETRY_INTERVAL = 1.0         # 開始できなかったときの再試行間隔 [秒]
MAX_CONSECUTIVE_FAILS = 120  # 連続でこの回数開始できなければ停止
TOKEN_TIMEOUT = 15           # 制御権が空くのを待つ時間 [秒]

# タスク開始（非公式）。開発者ツールで確認した内容に合わせて変更する
EXECUTION_PATH = "/desk/api/execution"
# =====================================


def log(msg):
    print(f"{datetime.now():%H:%M:%S}  {msg}", flush=True)


def encode_password(user, password):
    digest = hashlib.sha256(f"{password}#{user}@franka".encode("utf-8")).digest()
    return base64.encodebytes(",".join(str(b) for b in digest).encode()).decode()


class Desk:
    def __init__(self, ip, user, password):
        self.base = f"https://{ip}"
        self.user, self.password = user, password
        self.s = requests.Session()
        self.s.verify = False
        self.s.auth = (user, password)          # 公式 API: Basic 認証
        self.token = None
        self.ui = requests.Session()             # 画面用の通信: クッキー認証のみ（Basic 認証を付けると 401）
        self.ui.verify = False

    def _req(self, method, path, **kw):
        kw.setdefault("timeout", 10)
        return self.s.request(method, self.base + path, **kw)

    def _tok(self):
        return {"X-Control-Token": self.token} if self.token else {}

    # --- 状態 ---
    def status(self):
        def get(p):
            try:
                r = self._req("GET", p)
                return r.json() if r.ok else f"({r.status_code}) {r.text[:100]}"
            except Exception as e:
                return f"取得失敗: {e}"
        log(f"システム      : {get('/api/system')}")
        log(f"制御権        : {get('/api/system/control-token')}")
        log(f"関節          : {get('/api/arm/joints')}")
        log(f"セルフテスト  : {get('/api/safety/self-tests')}")
        log(f"要復帰エラー  : {get('/api/safety/recovery')}")

    # --- 制御権（公式 API） ---
    def take_control(self):
        r = self._req("GET", "/api/system/control-token")
        owner = r.json().get("owner") if r.ok else None
        if owner:
            log(f"制御権は '{owner}' が保持中。{TOKEN_TIMEOUT}秒待ちます"
                "（Desk 側に手放す確認が出たら許可してください）")
        r = self._req("POST", "/api/system/control-token:take",
                      json={"owner": "exhibition-loop", "timeout": TOKEN_TIMEOUT},
                      timeout=TOKEN_TIMEOUT + 10)
        if r.status_code != 200:
            raise RuntimeError(f"制御権の取得に失敗 ({r.status_code}): {r.text}")
        self.token = r.json()["token"]
        log("制御権を取得しました")

    def release_control(self):
        if not self.token:
            return
        try:
            r = self._req("POST", "/api/system/control-token:release", headers=self._tok())
            log("制御権を返しました" if r.ok else f"制御権の返却に失敗 ({r.status_code})")
        except requests.RequestException:
            pass
        self.token = None

    # --- 準備 ---
    def prepare(self, run_selftest):
        st = self._req("GET", "/api/safety/self-tests").json()
        log(f"セルフテスト: {st}")
        if st.get("status") in ("Warning", "Elapsed") and run_selftest:
            log("セルフテストを実行します（アームの電源が一度切れます）")
            r = self._req("POST", "/api/safety/self-tests:execute",
                          headers=self._tok(), timeout=300)
            log("セルフテスト完了" if r.ok else f"セルフテスト失敗 ({r.status_code}): {r.text}")

        mode = self._req("GET", "/api/system/operating-mode").json().get("status")
        if mode != "Execution":
            r = self._req("POST", "/api/system/operating-mode:change", headers=self._tok(),
                          json={"desiredOperatingMode": "Execution"})
            log(f"実行モードへ切替: {r.status_code} {r.text[:200]}")

        joints = self._req("GET", "/api/arm/joints").json()
        if any(j.get("brakeStatus") == "Locked" for j in joints):
            r = self._req("POST", "/api/arm/joints:unlock", headers=self._tok(), timeout=60)
            log("関節のロックを解除しました" if r.ok
                else f"ロック解除失敗 ({r.status_code}): {r.text[:200]}")

    # --- タスク開始（非公式） ---
    def ui_login(self):
        r = self.ui.post(self.base + "/admin/api/login", timeout=10,
                         json={"login": self.user,
                               "password": encode_password(self.user, self.password)})
        if r.status_code != 200:
            raise RuntimeError(f"画面用ログインに失敗 ({r.status_code}): {r.text[:200]}")
        self.ui.cookies.set("authorization", r.text.strip().strip('"'))
        log("画面用ログインOK")

    def start_task(self, task_id):
        if "authorization" not in self.ui.cookies:
            self.ui_login()
        r = self.ui.post(self.base + EXECUTION_PATH, data={"id": task_id}, timeout=10,
                         headers={**self._tok(),
                                  "Content-Type": "application/x-www-form-urlencoded",
                                  "Origin": self.base, "Referer": self.base + "/desk/"})
        if r.status_code == 401:                 # クッキーの期限切れなら取り直す
            self.ui.cookies.clear()
        return r.ok, f"{r.status_code}: {r.text[:200]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="状態を表示するだけ")
    ap.add_argument("--check", action="store_true", help="制御権を取って返すだけ")
    ap.add_argument("--selftest", action="store_true", help="期限が近ければセルフテストを実行")
    ap.add_argument("--count", type=int, default=0, help="実行回数（0 = 無限）")
    args = ap.parse_args()

    desk = Desk(ROBOT_IP, USERNAME, PASSWORD)
    if args.status:
        desk.status()
        return
    try:
        desk.take_control()
        if args.check:
            log("確認OK")
            return
        desk.prepare(args.selftest)

        done, fails, last = 0, 0, ""
        log(f"タスク '{TASK_NAME}' のループを開始（Ctrl+C で終了）")
        while args.count == 0 or done < args.count:
            ok, msg = desk.start_task(TASK_NAME)
            if ok:
                done, fails = done + 1, 0
                log(f"{done} 回目を開始")
                time.sleep(WAIT_AFTER_TASK)
                continue
            fails += 1
            if msg != last:
                log(f"開始待ち ({msg})")
                last = msg
            if fails >= MAX_CONSECUTIVE_FAILS:
                log("長時間開始できないので停止します")
                desk.status()
                break
            time.sleep(RETRY_INTERVAL)
    except KeyboardInterrupt:
        log("Ctrl+C で終了します（動作中ならロボット側の停止ボタンで止めてください）")
    except Exception as e:
        log(f"エラー: {e}")
        sys.exit(1)
    finally:
        desk.release_control()


if __name__ == "__main__":
    main()
