import os

def remove_newlines(text):
    """改行をすべて削除"""
    return text.replace("\n", "")

def main():
    # 読み込み元と保存先のパス
    base_dir = os.path.dirname(os.path.dirname(__file__))  # mojiokoshi/
    log_path = os.path.join(base_dir, "log", "scenario_log")
    print(f"読み込みディレクトリ: {log_path}")

    if not os.path.exists(log_path):
        print("❌ log/scenario_log が見つかりません")
        return

    files = os.listdir(log_path)
    print(f"発見ファイル一覧: {files}")

    for filename in files:
        if filename.endswith(".txt"):
            file_path = os.path.join(log_path, filename)
            print(f"Processing file: {file_path}")

            # ファイル読み込み
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 改行削除
            new_content = remove_newlines(content)

            # 上書き保存
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            print(f"✅ 改行を削除しました: {filename}")

    print("完了しました。")


if __name__ == "__main__":
    main()