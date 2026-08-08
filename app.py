"""m3u8-dl-hls-gui v0.28 - 兼容入口（重定向到 src.ui.app）"""
from src.ui.app import App, setup_logging

if __name__ == "__main__":
    setup_logging()
    app = App()
    app.mainloop()
