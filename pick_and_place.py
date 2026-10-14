#!/usr/bin/env python3
"""
ピック＆プレース 往復（Ctrl+C で止めるまで繰り返す）
  往路: 開く → 移動タスク1（start-move）→ 掴む → 移動タスク2（end-move）→ 離す
  復路: 移動タスク2（end-move）→ 掴む → 移動タスク1（start-move）→ 離す

準備:
    pip install requests pylibfranka
実行:
    python pick_and_place.py              # 止めるまで往復を繰り返す
    python pick_and_place.py --count 3    # 3 往復で終了
    python pick_and_place.py --homing     # 最初にグリッパーを初期化
    python pick_and_place.py --force 50   # 把持力を 50 N で掴む
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

PICK_TASK_ID = "0_start-move"   # 上空 → 把持位置（Desk の通信の id= の値）
PLACE_TASK_ID = "0_end-move"    # 把持位置 → 置く位置

# 各ステップの間の待ち時間 [秒]（番号は main() のコメントと対応）
# 移動タスク → ハンド操作（アームが止まるまで待つ）
WAIT_GO_START_MOVE = 2.2     # 2. start-move → 3. 掴む
WAIT_GO_END_MOVE = 3.0       # 4. end-move   → 5. 離す
WAIT_BACK_END_MOVE = 2.2     # 6. end-move   → 7. 掴む
WAIT_BACK_START_MOVE = 2.8   # 8. start-move → 9. 離す
# ハンド操作 → 移動タスク（ハンド操作は完了まで待つので 0 でよい）
WAIT_OPEN = 0.0              # 1. 開く → 2. start-move
WAIT_GO_GRASP = 0.0          # 3. 掴む → 4. end-move
WAIT_GO_RELEASE = 0.0        # 5. 離す → 6. end-move
WAIT_BACK_GRASP = 0.0        # 7. 掴む → 8. start-move
WAIT_BACK_RELEASE = 0.0      # 9. 離す → 次の往復の 2. start-move

OPEN_WIDTH = 0.05          # [m]
GRASP_WIDTH = 0.03         # 掴む物の幅 [m]
SPEED = 0.05               # 開く速度 [m/s]
GRASP_SPEED = 0.1          # 掴む速度 [m/s]（Franka Hand の上限は 0.1）
FORCE = 60.0               # 把持力の初期値 [N]（--force で指定可。連続把持は 70 N まで）
MAX_FORCE = 70.0
EPSILON = 0.01             # [m]
GRASP_RETRIES = 3          # 掴めなかったとき、直前の移動タスクからやり直す回数

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
                          json={"owner": "pick-and-place", "timeout": 15})
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


def wait(sec):
    if sec > 0:
        log(f"{sec:.1f} 秒待機")
        time.sleep(sec)


def run_task(desk, task_id, sec):
    if TOGGLE_FCI:
        desk.set_fci(False)
    log(f"タスク {task_id} を開始")
    desk.start_task(task_id)
    wait(sec)


def grasp(g, force):
    """掴む。物を持てていれば True、持てていなければ開いて False"""
    log(f"把持力 {force:.0f} N で掴む")
    ok = g.grasp(GRASP_WIDTH, GRASP_SPEED, force, EPSILON, EPSILON)
    try:
        w = g.read_once().width
    except Exception:
        w = None
    wtxt = f"{w * 1000:.1f} mm" if w is not None else "不明"
    if ok:
        log(f"掴む: 成功（指の幅 {wtxt}）")
        return True
    if w is not None and w > 0.003:
        log(f"掴む: 判定は失敗だが指の幅 {wtxt} で止まっているので続行します"
            f"（GRASP_WIDTH = {w:.3f} にすると成功判定になります）")
        return True
    log(f"掴む: 失敗（指の幅 {wtxt}）")
    g.move(OPEN_WIDTH, SPEED)
    return False


def reconnect_gripper(desk, g):
    if TOGGLE_FCI:
        desk.set_fci(True)
        return pylibfranka.Gripper(ROBOT_IP)   # FCI を切ると接続が切れるため作り直す
    return g


def move_and_grasp(desk, g, task_id, sec, force):
    """移動タスク → 掴む。掴めなければ移動タスクからやり直す。(g, 成功したか) を返す"""
    for attempt in range(GRASP_RETRIES + 1):
        if attempt:
            log(f"やり直し {attempt}/{GRASP_RETRIES}: タスク {task_id} から")
        run_task(desk, task_id, sec)
        g = reconnect_gripper(desk, g)
        if grasp(g, force):
            return g, True
    log(f"{GRASP_RETRIES} 回やり直しても掴めないので中止します")
    return g, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--homing", action="store_true", help="最初にグリッパーを初期化")
    ap.add_argument("--force", type=float, default=FORCE,
                    help=f"把持力 [N]（初期値 {FORCE}、上限 {MAX_FORCE}）")
    ap.add_argument("--count", type=int, default=0,
                    help="往復回数（0 なら Ctrl+C で止めるまで繰り返す）")
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
        wait(WAIT_OPEN)

        cycle = 0
        while args.count == 0 or cycle < args.count:
            cycle += 1
            log(f"===== {cycle} 往復目 =====")

            # ===== 往路: start → end =====
            # 2. 移動タスク1（上空 → 把持位置）
            # 3. 掴む（失敗したら 2. からやり直す）
            g, ok = move_and_grasp(desk, g, PICK_TASK_ID, WAIT_GO_START_MOVE, args.force)
            if not ok:
                return
            wait(WAIT_GO_GRASP)

            # 4. 移動タスク2（掴んだまま置く位置へ）
            run_task(desk, PLACE_TASK_ID, WAIT_GO_END_MOVE)

            # 5. 離す
            g = reconnect_gripper(desk, g)
            g.move(OPEN_WIDTH, SPEED)
            log("離す → 往路完了")
            wait(WAIT_GO_RELEASE)

            # ===== 復路: end → start =====
            # 6. 移動タスク2（end 側の把持位置へ）
            # 7. 掴む（失敗したら 6. からやり直す）
            g, ok = move_and_grasp(desk, g, PLACE_TASK_ID, WAIT_BACK_END_MOVE, args.force)
            if not ok:
                return
            wait(WAIT_BACK_GRASP)

            # 8. 移動タスク1（掴んだまま start 側へ）
            run_task(desk, PICK_TASK_ID, WAIT_BACK_START_MOVE)

            # 9. 離す
            g = reconnect_gripper(desk, g)
            g.move(OPEN_WIDTH, SPEED)
            log(f"離す → {cycle} 往復目完了")
            wait(WAIT_BACK_RELEASE)

    except KeyboardInterrupt:
        log("中断しました（動作中ならロボット側の停止ボタンで止めてください）")
    except Exception as e:
        log(f"エラー: {e}")
        sys.exit(1)
    finally:
        desk.release_control()


if __name__ == "__main__":
    main()
