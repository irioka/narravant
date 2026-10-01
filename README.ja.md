# NARRAVANT

[English](README.md)

現在のバージョン: **0.1.1**。

NARRAVANT は、小説や脚本のテキストからオーディオブック用のスクリプトを生成して朗読する OSS アプリケーションです。原文から [Fountain](https://fountain.io/) 形式のオーディオブック用スクリプトを生成し、Gemini TTS でナレーションと登場人物の声をリアルタイムに再生します。5 つの転換点と感情アークによる分析にも対応します。BGM・効果音には対応していません。

## スクリーンショット

<!--
  スクリーンショットは docs/images/ に配置します。UI は日本語表示でキャプチャし、
  英語版 README と画像を共有します。
-->

| 分析ワークベンチ | 5 つの転換点 |
| --- | --- |
| ![分析ワークベンチ：文書一覧、再生付き脚本エディタ、登場人物／声のペイン](docs/images/workbench.png) | ![物語の 5 つの転換点](docs/images/turning-points.png) |

### デモ

リアルタイム朗読 — Import・分析・声のデザイン・自動スクロール付き再生:

https://github.com/user-attachments/assets/511a7b1d-07ab-4e1b-8aa5-9045ebe5dd8c

> ここで示すサンプル作品は、著作権が消滅した日本語文学のライブラリ [青空文庫](https://www.aozora.gr.jp/) のものです。

## 主な機能

- **スクリプト生成** — 小説または脚本（`.txt` / `.pdf` / `.fountain` / `.fdx` / Native JSON）をアップロードし、ナレーションと台詞からなるオーディオブック用スクリプトを Fountain 形式で生成します。
- **物語の分析** — アップロードした作品から、あらすじ・5 つの転換点・感情アークを生成して保持します。
- **声のデザイン** — ナレーターと各登場人物の声の特徴を生成し、Gemini で声を作成して話者ごとに割り当てます。
- **リアルタイム再生** — 各発話を脚本順に Gemini TTS で音声化し、WebSocket でストリーミング再生します。生成音声は保存せず、再生のたびに生成します。
- **ローカル保存** — スクリプト・声の特徴・声の割り当て・分析をローカルのデータベースに保存します。Native JSON で Export・Import できます。
- **日英切替 UI** — 画面表示を英語・日本語で切り替えられます。

## 技術スタック

- **バックエンド** — Python 3.12+、FastAPI、SQLite（Valence 類似検索に `sqlite-vec` を使用）、Google Gemini（分析・Voice Design・TTS）。
- **フロントエンド** — React 19、TypeScript、Vite、Tailwind CSS、i18next（英語 / 日本語）。
- **ツール** — バックエンドは `uv`、フロントエンドは `npm`、セットアップ・起動・テスト・lint は `make` を使用。

## 前提条件

- **Python 3.12+** と [`uv`](https://docs.astral.sh/uv/)。
- **Node.js 24**（`.nvmrc` を参照）と `npm`。
- **`make`**（以下のセットアップ・起動コマンドで使用）。
- 課金を有効にした **Google Gemini Developer API キー**。

## セットアップと起動手順

### 1. 環境設定（.env）
`.env.example` をコピーして `.env` を作成し、必須の環境変数を設定します。
```bash
cp .env.example .env
```
- `GEMINI_API_KEY`: **必須**。Gemini Developer API の API キーを指定します。
- `GEMINI_MODEL`: 原稿分析・脚色用モデル（既定: `gemini-3.8-flash`）。
- `GEMINI_TTS_MODEL`: 音声合成モデル（既定: `gemini-3.8-flash-tts`）。
- `SQLITE_DB_PATH`: SQLite データベースファイルのパス（既定: `./runtime/narravant.sqlite3`）。相対パスは `.env` があるリポジトリルートから解決されます。
- `LOCAL_STORAGE_PATH`: スクリプト保存ディレクトリ（既定: `./runtime/storage`）。相対パスは `.env` があるリポジトリルートから解決されます。

再生中の TTS 再試行回数と Scene Heading 間の無音時間は、`backend/config.yaml` の `playback`（`tts_max_attempts: 6`、`scene_pause_duration_ms: 3000`）で設定します。

> [!WARNING]
> **API 利用料金について**
> Gemini API（原稿分析、Voice Design による声の作成、TTS 音声合成）の呼び出しでは、Google Cloud / Gemini Developer API の料金が発生します。アプリ内に費用の見積もりや残高表示 UI はありませんので、利用状況は Google AI Studio のダッシュボード等でご確認ください。

### 2. 起動手順（2プロセス）
バックエンド（FastAPI: 8000）とフロントエンド（Vite: 5173）をまとめて起動します。

```bash
# 依存関係のセットアップ
make init

# 2 プロセスを起動
make run
```
起動後、ブラウザで `http://localhost:5173` にアクセスします。

各プロセスを個別に起動する場合は `make dev-backend`（FastAPI: 8000）と `make dev-frontend`（Vite: 5173）を使います。

### 3. 基本的な使い方

1. 小説または脚本（`.txt` / `.pdf` / `.fountain` / `.fdx` / Native JSON）を **Import** します。NARRAVANT が Fountain 形式のオーディオブック用スクリプトを生成し、物語を分析します。
2. **分析結果を確認** します（あらすじ・5 つの転換点・感情アーク）。必要に応じて編集・保存します。
3. **声をデザイン** します。ナレーターと各登場人物の声の特徴を記述し、Gemini で声を生成して話者ごとに割り当てます。
4. **再生** します。現在の段落からリアルタイム再生を開始します。エディタは朗読中のテキストをハイライトし、自動スクロールします。
5. Native JSON で **Export / Import** し、作品をローカルデータベース間で移動できます。

## 開発

- `make test` — バックエンド（`pytest`）とフロントエンド（`vitest`）のテストを実行します。
- `make lint` — `ruff`（バックエンド）とフロントエンドの lint を実行します。
- `make clean` — ローカルの仮想環境・`node_modules`・ビルド出力・ローカル DB を削除します。

## プロジェクト構成

```
backend/    FastAPI サービス：API、分析・脚色、Voice Design、TTS、ローカル保存
frontend/   React + Vite の Web クライアント（分析ワークベンチ、再生、日英 UI）
VERSION     リリース番号の正本
```

## バージョニング

本プロジェクトは [セマンティックバージョニング](https://semver.org/lang/ja/) に従います。リリース番号は [`VERSION`](VERSION) に定義し、リリース内容は [`CHANGELOG.md`](CHANGELOG.md) を参照してください。

## ライセンス

ソフトウェアには [MIT License](LICENSE)（Copyright (c) 2026 Masayuki Irioka）を採用しています。入力した本、第三者の素材、生成音声の利用権はこのライセンスでは付与されません。翻案・配布する作品の権利確認は利用者が行ってください。
