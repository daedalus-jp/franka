# Franka FR3 ピック＆プレース

## 本番では pick_and_place.py を使う

本番（デモ・展示）で動かすのは **`pick_and_place.py`** です。
ほかのスクリプトは動作確認や開発途中のもので、本番では使いません。

## 事前準備

- Desk の SETTINGS → Network が下の画像の設定になっていることを確認する
  - X5 - Robot network: `192.168.0.1`
  - C2 - Shop Floor network: DHCP Client オフ、Address `192.168.100.49`、Netmask `255.255.255.0`、Gateway `192.168.100.1`

  ![Desk のネットワーク設定](docs/desk_network.png)

- PC の Ethernet の設定が下の画像の設定になっていることを確認する（Windows の設定 → ネットワークとインターネット → イーサネット）
  - IP 割り当て: 手動、IPv4 アドレス `192.168.100.50`、IPv4 マスク `255.255.255.0`

  ![PC の Ethernet 設定](docs/pc_ethernet.png)

## 起動方法

1. Control（四角い箱）の電源を入れる
2. Control の C2 ポートと PC を LAN ケーブルで直結する
3. ブラウザで https://192.168.100.49/desk/ を開けるか確認する
4. 起動する
   ```
   python pick_and_place.py --homing
   ```
   電源を入れた後は、必ず `--homing` を付けてグリッパーを初期化する（付けないと指の幅がずれて、開かなくなることがある）
5. 動かなければ、Desk の画面に出る「Grant」を押して制御を許可する

## 停止方法

```
python pack_pose.py
```

先に Franka Hand を外すこと。詳しくは下の「[片付け（パックポーズ）](#片付けパックポーズ)」を参照。

## オプション

```
pip install requests pylibfranka
python pick_and_place.py              # Ctrl+C で止めるまで往復を繰り返す
python pick_and_place.py --count 3    # 3 往復で終了
python pick_and_place.py --homing     # 最初にグリッパーを初期化
python pick_and_place.py --force 50   # 把持力を 50 N で掴む（上限 70 N）
```

## 動作

1 往復の流れ:

1. 開く（最初の 1 回だけ）
2. `0_start-move` → 掴む → `0_end-move` → 離す（往路）
3. `0_end-move` → 掴む → `0_start-move` → 離す（復路）

- 掴めなかったときは、ハンドを開いて直前の移動タスクからやり直します（最大 `GRASP_RETRIES` 回）。
- それでも掴めないとき、またはエラーが出たときは止まり、制御権を返却します。
- 移動タスクの後の待ち時間などは、ファイル先頭の設定欄（`WAIT_...`）で調整します。

## 途中で止めるとき

Ctrl+C で止まります。ただし Desk のタスクが動いている途中なら、アームはそのタスクを最後まで続けることがあります。
すぐに止めたいときは、ロボット側の停止ボタンを使ってください。

## 片付け（パックポーズ）

終了時は `pack_pose.py` でアームを折り畳み姿勢（パックポーズ）にしてから電源を切ります。

**先に Franka Hand を外してください。** 付けたままだと本体にぶつかる恐れがあります。
実行すると確認が出るので、ハンドを外してロボットの周りに何もないことを確かめてから `y` を入力します。

```
python pack_pose.py              # パックポーズへ移動 → 関節をロック（通常はこれ）
python pack_pose.py --no-lock    # パックポーズへ移動するだけ（ロックしない）
python pack_pose.py --lock-only  # 移動せず、今の姿勢で関節をロックするだけ（確認なし）
```

ロックまで終わったら、Desk のメニューから Shut down します。

## そのほかのスクリプト（本番では使わない）

| ファイル | 内容 |
| --- | --- |
| `gripper_test.py` | Franka Hand を PC から開閉できるかの確認 |
| `move_and_grasp.py` | 移動タスク 1 回 → 掴む、までの確認用 |
| `desk_task_loop.py` | Desk のタスクを繰り返し実行するだけ（グリッパー操作なし） |
