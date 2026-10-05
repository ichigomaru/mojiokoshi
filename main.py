import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
from src.gui import MojiOkoshiGUI

def main():
    app = MojiOkoshiGUI()
    app.run()  

if __name__ == "__main__":
    main()