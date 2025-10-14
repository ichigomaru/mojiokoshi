import sys
import os
# Ensure src is in sys.path for import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.gui import MojiOkoshiGUI

def main():
    # GUIクラスを生成して mainloop を実行
    app = MojiOkoshiGUI()
    app.run()  # run() 内で root.mainloop() を呼ぶ想定

if __name__ == "__main__":
    main()