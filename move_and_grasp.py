#!/usr/bin/env python3
"""
Desk のタスク（上空 → 把持位置）を実行し、終わったら Franka Hand で掴む

準備:
    pip install requests pylibfranka
実行:
    python move_and_grasp.py              # 開く → 移動タスク → 掴む
    python move_and_grasp.py --homing     # 最初にグリッパーを初期化
    python move_and_grasp.py --release    # 最後に 3 秒後に離す（テスト用）
    python move_and_grasp.py --force 50   # 把持力を 50 N で掴む
"""

import argparse
import base64
import hashlib
import sys
import time
from datetime import datetime

import pylibfranka
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ===== 設定 =====
ROBOT_IP = "192.168.100.49"
USERNAME = "admin"
PASSWORD = "taroworld"

TASK_ID = "0_start-move"   # Desk で RUN したときの通信の id= の値
MOVE_DURATION = 8.0        # タスクの動作にかかる秒数（実測 + 余裕）

OPEN_WIDTH = 0.08          # [m]
GRASP_WIDTH = 0.03         # 掴む物の幅 [m]
SPEED = 0.05               # [m/s]
FORCE = 40.0               # 把持力の初期値 [N]（--force で指定可。連続把持は 70 N まで）
MAX_FORCE = 70.0
EPSILON = 0.01             # [m]

TOGGLE_FCI = False         # FCI 有効中にタスクが動かない場合は True
# ================


def log(msg):
    print(f"{datetime.now():%H:%M:%S}  {msg}", flush=True)


def encode_password(user, password):
    digest = hashlib.sha256(f"{password}#{user}@franka".encode("utf-8")).digest()
    return base64.encodebytes(",".join(str(b) for b in digest).encode()).decode()


class Desk:
    def __init__(self):
        self.base = f"https://{ROBOT_IP}"
        self.api = requests.Session()          # 公式 API: Basic 認証
        self.api.verify = False
        self.api.auth = (USERNAME, PASSWORD)
        self.ui = requests.Session()           # タスク開始: クッキー認証のみ
        self.ui.verify = False
        self.token = None

    def _tok(self):
        return {"X-Control-Token": self.token} if self.token else {}

    def post(self, path, **kw):
        kw.setdefault("timeout", 30)
        return self.api.post(self.base + path, headers=self._tok(), **kw)

    def take_control(self):
        r = self.api.post(self.base + "/api/system/control-token:take", timeout=40,
                          json={"owner": "move-and-grasp", "timeout": 15})
        if r.status_code != 200:
            raise RuntimeError(f"制御権の取得に失敗 ({r.status_code}): {r.text} "
                               "→ Desk で制御権を手放すか、Desk を開いて要求を許可してください")
        self.token = r.json()["token"]
        log("制御権を取得")

    def release_control(self):
        if self.token:
            try:
                self.post("/api/system/control-token:release", timeout=10)
                log("制御権を返却")
            except requests.RequestException:
                pass
            self.token = None

    def set_fci(self, active):
        r = self.post("/api/fci:activate" if active else "/api/fci:deactivate")
        if not r.ok:
            raise RuntimeError(f"FCI 切替に失敗 ({r.status_code}): {r.text[:200]}")
        time.sleep(1.0)

    def prepare(self):
        """実行モード・関節ロック解除・FCI 有効化をまとめて行う"""
        mode = self.api.get(self.base + "/api/system/operating-mode", timeout=10).json().get("status")
        if mode != "Execution":
            r = self.post("/api/system/operating-mode:change",
                          json={"desiredOperatingMode": "Execution"})
            log(f"実行モードへ切替: {'OK' if r.ok else r.text[:200]}")
        joints = self.api.get(self.base + "/api/arm/joints", timeout=10).json()
        if any(j.get("brakeStatus") == "Locked" for j in joints):
            r = self.post("/api/arm/joints:unlock", timeout=60)
            log(f"関節ロック解除: {'OK' if r.ok else r.text[:200]}")
        fci = self.api.get(self.base + "/api/fci", timeout=10).json().get("status")
        if fci != "Active":
            log("FCI を有効化")
            self.set_fci(True)

    def start_task(self, task_id, timeout=30.0):
        if "authorization" not in self.ui.cookies:
            r = self.ui.post(self.base + "/admin/api/login", timeout=10,
                             json={"login": USERNAME,
                                   "password": encode_password(USERNAME, PASSWORD)})
            if r.status_code != 200:
                raise RuntimeError(f"画面用ログインに失敗 ({r.status_code}): {r.text[:200]}")
            self.ui.cookies.set("authorization", r.text.strip().strip('"'))

        deadline = time.time() + timeout
        while True:
            r = self.ui.post(self.base + "/desk/api/execution", data={"id": task_id},
                             timeout=10,
                             headers={**self._tok(), "Origin": self.base,
                                      "Referer": self.base + "/desk/"})
            if r.ok:
                return
            if time.time() > deadline:
                raise RuntimeError(f"タスクを開始できません ({r.status_code}): {r.text[:200]}")
            time.sleep(0.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--homing", action="store_true", help="最初にグリッパーを初期化")
    ap.add_argument("--release", action="store_true", help="掴んだ 3 秒後に離す")
    ap.add_argument("--force", type=float, default=FORCE,
                    help=f"把持力 [N]（初期値 {FORCE}、上限 {MAX_FORCE}）")
    args = ap.parse_args()
    if not 0 < args.force <= MAX_FORCE:
        sys.exit(f"--force は 0 より大きく {MAX_FORCE} N 以下で指定してください")

    desk = Desk()
    try:
        desk.take_control()
        desk.prepare()

        # 1. ハンドを開いておく
        log("グリッパーに接続")
        g = pylibfranka.Gripper(ROBOT_IP)
        if args.homing:
            log("グリッパー初期化")
            g.homing()
        log("開く")
        g.move(OPEN_WIDTH, SPEED)

        # 2. 移動タスク（上空 → 把持位置）
        if TOGGLE_FCI:
            desk.set_fci(False)
        log(f"タスク {TASK_ID} を開始")
        desk.start_task(TASK_ID)
        log(f"動作完了まで {MOVE_DURATION:.0f} 秒待機")
        time.sleep(MOVE_DURATION)

        # 3. 掴む
        if TOGGLE_FCI:
            desk.set_fci(True)
            g = pylibfranka.Gripper(ROBOT_IP)   # FCI を切ると接続が切れるため作り直す
        log(f"把持力 {args.force:.0f} N で掴む")
        ok = g.grasp(GRASP_WIDTH, SPEED, args.force, EPSILON, EPSILON)
        log(f"掴む: {'成功' if ok else '失敗（物の位置・GRASP_WIDTH を確認）'}")

        if args.release:
            time.sleep(3.0)
            g.move(OPEN_WIDTH, SPEED)
            log("離す")

    except KeyboardInterrupt:
        log("中断しました（動作中ならロボット側の停止ボタンで止めてください）")
    except Exception as e:
        log(f"エラー: {e}")
        sys.exit(1)
    finally:
        desk.release_control()


if __name__ == "__main__":
    main()
