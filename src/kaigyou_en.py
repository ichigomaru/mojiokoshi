import os

def insert_newlines(text, sentence_terminators=("。", "！", "？", ".", "!", "?")):
    """文末記号の後に改行を入れる"""
    new_text = ""
    for char in text:
        new_text += char
        if char in sentence_terminators:
            new_text += "\n"
    # 特定の記号を削除
    new_text = new_text.replace("[", "").replace("]", "")
    return new_text


def main():
    # 読み込み元と保存先のパス
    base_dir = os.path.dirname(os.path.dirname(__file__))  # mojiokoshi/
    log_path = os.path.join(base_dir, "log", "scenario_log")

    # 出力ディレクトリ作成
    output_path = os.path.join(log_path, "output")
    os.makedirs(output_path, exist_ok=True)

    # log/scenario_log 内の .txt ファイルを探す
    for filename in os.listdir(log_path):
        if filename.endswith(".txt"):
            file_path = os.path.join(log_path, filename)

            # ファイル読み込み
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 改行を挿入
            new_content = insert_newlines(content)

            # 出力先ファイルパス
            output_file = os.path.join(output_path, filename)
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(new_content)

            print(f"改行を追加し、出力しました: {output_file}")


if __name__ == "__main__":
    main()