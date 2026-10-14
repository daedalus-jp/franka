# Franka FR3 ピック＆プレース

## 本番では pick_and_place.py を使う

本番（デモ・展示）で動かすのは **`pick_and_place.py`** です。
ほかのスクリプトは動作確認や開発途中のもので、本番では使いません。

```
pip install requests pylibfranka
python pick_and_place.py              # Ctrl+C で止めるまで往復を繰り返す
python pick_and_place.py --count 3    # 3 往復で終了
python pick_and_place.py --homing     # 最初にグリッパーを初期化
python pick_and_place.py --force 50   # 把持力を 50 N で掴む（上限 70 N）
```

### 動作

1 往復の流れ:

1. 開く（最初の 1 回だけ）
2. `0_start-move` → 掴む → `0_end-move` → 離す（往路）
3. `0_end-move` → 掴む → `0_start-move` → 離す（復路）

- 掴めなかったときは、ハンドを開いて直前の移動タスクからやり直します（最大 `GRASP_RETRIES` 回）。
- それでも掴めないとき、またはエラーが出たときは止まり、制御権を返却します。
- 移動タスクの後の待ち時間などは、ファイル先頭の設定欄（`WAIT_...`）で調整します。

### 止めるとき

Ctrl+C で止まります。ただし Desk のタスクが動いている途中なら、アームはそのタスクを最後まで続けることがあります。
すぐに止めたいときは、ロボット側の停止ボタンを使ってください。

## そのほかのスクリプト（本番では使わない）

| ファイル | 内容 |
| --- | --- |
| `gripper_test.py` | Franka Hand を PC から開閉できるかの確認 |
| `move_and_grasp.py` | 移動タスク 1 回 → 掴む、までの確認用 |
| `desk_task_loop.py` | Desk のタスクを繰り返し実行するだけ（グリッパー操作なし） |
| `pack_pose.py` | 片付け用。折り畳み姿勢にして関節をロック（先にハンドを外すこと） |
