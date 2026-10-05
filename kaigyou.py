import os

def insert_newlines(text, sentence_terminators=("。", "！", "？", ".", "!", "?")):
    """文末記号の後に改行を入れる"""
    new_text = ""
    for char in text:
        new_text += char
        if char in sentence_terminators:
            new_text += "\n"
    # 特定の記号を削除
    new_text = new_text.replace("[", "").replace("]", "").replace(",", "").replace(".", "").replace("'", "")
    return new_text


def main():
    # 処理したい .txt ファイルのパスを直接指定
    file_path = "/Users/ichigomaru/Documents/python/mojiokoshi/log/scenario_log/other/2026-06-17_20-55-25.txt"

    # ファイルが実際に存在するか念のためチェック
    if not os.path.isfile(file_path):
        print(f"エラー: 指定されたファイルが見つかりません。パスを確認してください。\n{file_path}")
        return

    # ファイル読み込み
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 改行を挿入・特定の記号を削除
    new_content = insert_newlines(content)

    # 元のファイルに上書き保存
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"改行を追加し、出力しました: {file_path}")


if __name__ == "__main__":
    main()