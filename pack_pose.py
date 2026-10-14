#!/usr/bin/env python3
"""
FR3 を折り畳み姿勢（パックポーズ）にする（公式 Desk API: /api/arm/motion:start）

注意: 公式ドキュメントでは、パックポーズへ動かす前にエンドエフェクタ（Franka Hand）を
      外す必要があると書かれています。付けたままだと本体にぶつかる恐れがあります。

実行:
    python pack_pose.py              # パックポーズへ移動 → 関節をロック
    python pack_pose.py --no-lock    # パックポーズへ移動するだけ（ロックしない）
    python pack_pose.py --lock-only  # 移動せず、今の姿勢で関節をロックするだけ
（このあと Desk のメニューから Shut down してください）
"""

import argparse
import sys

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROBOT_IP = "192.168.100.49"
USERNAME = "admin"
PASSWORD = "taroworld"


def main():
    ap = argparse.ArgumentParser()
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--no-lock", action="store_true", help="パックポーズへ移動するだけでロックしない")
    grp.add_argument("--lock-only", action="store_true", help="移動せず、関節をロックするだけ")
    args = ap.parse_args()

    if not args.lock_only:
        ans = input("ハンドを外し、ロボットの周りに何もないことを確認しましたか？ [y/N] ")
        if ans.strip().lower() != "y":
            sys.exit("中止しました")

    base = f"https://{ROBOT_IP}"
    s = requests.Session()
    s.verify = False
    s.auth = (USERNAME, PASSWORD)

    r = s.post(base + "/api/system/control-token:take", timeout=40,
               json={"owner": "pack-pose", "timeout": 15})
    if r.status_code != 200:
        sys.exit(f"制御権の取得に失敗 ({r.status_code}): {r.text}")
    tok = {"X-Control-Token": r.json()["token"]}

    try:
        if not args.lock_only:
            if s.get(base + "/api/fci", timeout=10).json().get("status") == "Active":
                s.post(base + "/api/fci:deactivate", headers=tok, timeout=30)
                print("FCI を無効化しました")

            mode = s.get(base + "/api/system/operating-mode", timeout=10).json().get("status")
            if mode != "Execution":
                s.post(base + "/api/system/operating-mode:change", headers=tok, timeout=30,
                       json={"desiredOperatingMode": "Execution"})

            print("パックポーズへ移動中…（止めるときはロボットの停止ボタン）")
            r = s.post(base + "/api/arm/motion:start", headers=tok, timeout=180,
                       json={"type": "MoveToPackPose"})
            if r.status_code == 204:
                print("移動完了")
            else:
                sys.exit(f"移動に失敗 ({r.status_code}): {r.text}")

        if args.no_lock:
            print("ロックはしていません（--no-lock）")
            return

        r = s.post(base + "/api/arm/joints:lock", timeout=60)
        if r.ok:
            print("関節をロックしました。Desk のメニューから Shut down してください")
        else:
            print(f"ロックに失敗 ({r.status_code}): {r.text}  → Desk で関節をロックしてください")
    finally:
        s.post(base + "/api/system/control-token:release", headers=tok, timeout=10)
        print("制御権を返しました")


if __name__ == "__main__":
    main()
