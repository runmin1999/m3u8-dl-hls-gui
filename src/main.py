"""m3u8-dl-hls-gui 入口点"""

import os
import sys

# 确保 src 目录在 sys.path 中
_src_dir = os.path.dirname(os.path.abspath(__file__))
_project_dir = os.path.dirname(_src_dir)
sys.path.insert(0, _project_dir)

from src.ui.app import App


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
