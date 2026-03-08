# Ping Monitor

Ping 監視プログラム。設定されたホストに対して定期的に ping を送信し、連続ロストが閾値に達すると Discord Webhook で通知を送信します。Web 画面で監視状況を確認できます。

## 機能

- **Ping 監視**: 設定されたターゲットに対して定期的に ping を送信
- **アラート発報**: 連続ロスト数が閾値に達するとアラート状態に
- **Discord Webhook 通知**: アラート発生時と回復時に Discord に通知
- **Web 監視画面**: ブラウザで監視状況を確認（30 秒ごとの自動更新）
- **ソート表示**: lost_count の多い順に表示、同じ場合はホスト名順

## 要件

- Python 3.6 以上
- Linux または macOS
- 外部ライブラリは不要（Python 標準ライブラリのみ）

## インストール

1. リポジトリをクローン
   ```bash
   git clone <repository-url>
   cd simple-mon
   ```

2. 設定ファイルを編集
   ```bash
   nano config.json
   ```

## 設定ファイル

`config.json` で監視対象、間隔、閾値、Discord Webhook URL を設定します。

```json
{
  "monitor": {
    "interval": 5,
    "alert_threshold": 3
  },
  "targets": [
    {
      "host": "8.8.8.8",
      "name": "Google DNS"
    },
    {
      "host": "www.example.com",
      "name": "Example"
    }
  ],
  "discord": {
    "webhook_url": "https://discord.com/api/webhooks/..."
  }
}
```

### 設定項目

| 項目 | 説明 | 必須 |
|------|------|------|
| `monitor.interval` | ping の間隔（秒） | いいえ（デフォルト：5） |
| `monitor.alert_threshold` | 何回連続ロストしたらアラート | いいえ（デフォルト：3） |
| `targets` | 監視対象のリスト | はい |
| `targets[].host` | ホスト名または IP アドレス | はい |
| `targets[].name` | 表示名 | いいえ（デフォルト：host） |
| `discord.webhook_url` | Discord Webhook URL | いいえ（空の場合は通知なし） |

## 使い方

### 起動

```bash
# デフォルト設定（config.json, ポート 8080）
python3 ping_monitor.py

# 設定ファイルとポートを指定
python3 ping_monitor.py --config /path/to/config.json --port 9090

# 短縮形
python3 ping_monitor.py -c config.json -p 8080
```

### Web 画面

ブラウザで `http://localhost:8080`（または指定したポート）にアクセスします。

表示内容：
- ホスト名
- 表示名
- ステータス（OK/ALERT）
- 連続ロスト数
- 応答時間（ms）
- 最終チェック時刻

アラート状態の行は赤色背景で表示されます。

## Discord Webhook の設定

1. Discord サーバーで通知を送りたいチャンネルを選択
2. チャンネル設定 → Webhooks → Webhook を作成
3. Webhook URL をコピー
4. `config.json` の `discord.webhook_url` に設定

### 通知メッセージ例

- **アラート時**: `[Alert] Google DNS: 3 consecutive ping losses`
- **回復時**: `[Recovery] Google DNS: Connection restored`

## Docker で利用

### イメージのビルド

```bash
docker build -t ping-monitor .
```

### コンテナの起動

```bash
# デフォルト設定で起動（ポート 8080）
docker run -d --name ping-monitor -p 8080:8080 ping-monitor

# 設定ファイルをマウントして起動
docker run -d --name ping-monitor \
  -v $(pwd)/config.json:/app/config.json \
  -p 8080:8080 ping-monitor
```

### コンテナの停止

```bash
docker stop ping-monitor
docker rm ping-monitor
```

### 画像情報

- **ベースイメージ**: Python 3.11 slim
- **サイズ**: 約 153MB
- **ユーザー**: 非 root ユーザー（monitor）
- **ヘルスチェック**: 内蔵（30 秒間隔）

**注意事項**:
- Docker イメージには `ca-certificates` が含まれているため、Discord Webhook が正常に動作します
- 設定ファイルを変更する場合は、ボリュームマウントを使用してください

## コマンドラインオプション

| オプション | 説明 | デフォルト |
|-----------|------|-----------|
| `--config`, `-c` | 設定ファイルのパス | config.json |
| `--port`, `-p` | Web サーバーのポート番号 | 8080 |

## 注意事項

### メモリ使用量

- 定期的なガベージコレクションを実装しているため、長期間動作させてもメモリリークしません
- 約 100 回の監視ループごとにガベージコレクションが実行されます

### OS 互換性

- **Linux**: 正常に動作
- **macOS**: 正常に動作（ping 出力形式の違いを自動検出）
- **Windows**: 未対応（ping コマンドの違いのため）

### 監視間隔

- 監視間隔は最短 1 秒を推奨（それ以下にすると CPU 使用率が上昇する可能性があります）

## ファイル構成

```
simple-mon/
├── config.json              # 設定ファイル
├── ping_monitor.py          # メインプログラム
├── Dockerfile               # Docker イメージ定義
├── .dockerignore            # Docker ビルド除外ファイル
├── README.md                # このファイル
└── templates/
    └── index.html           # Web 監視画面
```

## ライセンス

MIT License