# AI議事録アシスタント

音声ファイルから自動的に議事録を作成するStreamlitアプリケーションです。

## 機能

- 音声ファイルの文字起こし
- 商談内容の自動分析
- 議事録の自動生成
- レポートの保存と管理

## セットアップ

### 1. 依存関係のインストール

```bash
pip install -r requirements.txt
```

### 2. APIキーの設定

1. `.streamlit/secrets.toml.example`をコピーして`.streamlit/secrets.toml`にリネーム
2. 以下のAPIキーを設定：
   - `HF_TOKEN`: Hugging Faceのトークン
   - `OPENAI_API_KEY`: OpenAIのAPIキー

### 3. アプリケーションの実行

```bash
streamlit run app.py
```

## 注意事項

- 機密情報（APIキーなど）は`.streamlit/secrets.toml`に保存し、Gitにコミットしないでください
- 音声ファイルは一時的に処理され、保存されません
- データベースファイル（`database.db`）は自動的に作成されます

## ライセンス

このプロジェクトはMITライセンスの下で公開されています。 