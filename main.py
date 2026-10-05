import sys
import os
import atexit
import re
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# macOS の日本語入力 (TSM/IMK) が stderr に直接出す無害なログ。表示しない
NOISY_STDERR_PATTERNS = (
    b"TSM AdjustCapsLockLEDForKeyTransitionHandling",
    b"IMKCFRunLoopWakeUpReliable",
)


def filter_stderr():
    """
    stderr (fd 2) をパイプ経由にして、NOISY_STDERR_PATTERNS を含む行だけ捨てる。
    macOS のフレームワークは Python を通さず fd 2 に直接書くので、fd ごと差し替える。
    """
    original_fd = os.dup(2)
    read_fd, write_fd = os.pipe()
    os.dup2(write_fd, 2)
    os.close(write_fd)

    def forward():
        # 進捗バー (tqdm) は改行せず \r で書き換えるので、\n と \r の両方で区切ってすぐ流す
        pending = b""
        while True:
            chunk = os.read(read_fd, 4096)
            if not chunk:
                break
            pending += chunk
            cut = max(pending.rfind(b"\n"), pending.rfind(b"\r")) + 1
            if cut == 0:
                continue
            for piece in re.split(rb"(?<=[\r\n])", pending[:cut]):
                if piece and not any(p in piece for p in NOISY_STDERR_PATTERNS):
                    os.write(original_fd, piece)
            pending = pending[cut:]
        if pending and not any(p in pending for p in NOISY_STDERR_PATTERNS):
            os.write(original_fd, pending)
        os.close(read_fd)

    thread = threading.Thread(target=forward, daemon=True)
    thread.start()

    def restore():
        # 残りの出力 (終了時のエラー表示など) を流し切ってから元に戻す
        try:
            sys.stderr.flush()
        except Exception:
            pass
        os.dup2(original_fd, 2)  # パイプの書き込み側が閉じ、forward が終わる
        thread.join(timeout=1)

    atexit.register(restore)


def main():
    filter_stderr()
    from src.gui import MojiOkoshiGUI
    app = MojiOkoshiGUI()
    app.run()

if __name__ == "__main__":
    main()
