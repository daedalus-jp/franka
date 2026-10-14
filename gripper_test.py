#!/usr/bin/env python3
"""
Franka Hand をPCから開閉できるか確認するテスト

準備（WSL2 の Ubuntu 22.04 以降）:
    pip install pylibfranka

実行:
    python gripper_test.py              # 初期化 → 開く → 掴む → 開く
    python gripper_test.py --no-homing  # 初期化を飛ばす
"""

import argparse
import time

import pylibfranka

ROBOT_IP = "192.168.100.49"

OPEN_WIDTH = 0.08    # 開いたときの幅 [m]（最大 0.08）
GRASP_WIDTH = 0.03   # 掴む物の幅 [m]（積み木なら実寸を入れる）
SPEED = 0.05         # 指の速さ [m/s]
FORCE = 20.0         # 把持力 [N]（軽い物なら 10〜30 程度）
EPSILON = 0.01       # 幅の許容誤差 [m]


def show_state(g):
    try:
        s = g.read_once()
        print(f"  幅 {s.width * 1000:.1f} mm / 最大 {s.max_width * 1000:.1f} mm / "
              f"掴んでいる: {s.is_grasped} / 温度 {s.temperature}℃")
    except Exception as e:
        print(f"  状態の読み取りに失敗（開閉はできている可能性あり）: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-homing", action="store_true")
    args = ap.parse_args()

    print(f"{ROBOT_IP} のグリッパーに接続します…")
    g = pylibfranka.Gripper(ROBOT_IP)
    print(f"接続OK（サーバーバージョン {g.server_version()}）")
    show_state(g)

    if not args.no_homing:
        print("初期化（全開→全閉→全開）")
        g.homing()
        show_state(g)

    print("開く")
    g.move(OPEN_WIDTH, SPEED)
    show_state(g)

    input("指の間に物を置いて Enter を押すと掴みます（何も置かなければ失敗扱いになるだけです）")
    ok = g.grasp(GRASP_WIDTH, SPEED, FORCE, EPSILON, EPSILON)
    print(f"掴む: {'成功' if ok else '失敗（幅の設定と物の大きさを確認）'}")
    show_state(g)

    time.sleep(1.0)
    print("開く")
    g.move(OPEN_WIDTH, SPEED)
    show_state(g)
    print("テスト完了")


if __name__ == "__main__":
    main()
