"""m3u8 下载工具 - 主入口"""

import os
import sys
import logging
import argparse
import shutil

import requests

from m3u8_parser import parse_m3u8, M3U8Playlist
from downloader import download_all
from decryptor import decrypt_files
from merger import merge_to_ts


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def fetch_m3u8(url: str, headers: dict = None, proxy: str = "") -> str:
    """获取 m3u8 文件内容"""
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        **(headers or {}),
    }
    proxies = {"http": proxy, "https": proxy} if proxy else None
    resp = requests.get(url, headers=req_headers, timeout=30, proxies=proxies)
    resp.raise_for_status()
    return resp.text


def get_base_url(url: str) -> str:
    """从完整 URL 中提取基础 URL（去掉最后一段路径）"""
    return url.rsplit("/", 1)[0] + "/"


def format_duration(seconds: float) -> str:
    """格式化时长"""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"


def print_progress(completed: int, total: int):
    """打印下载进度（每 100 个分片或完成时输出一次）"""
    if completed % 100 == 0 or completed == total:
        ratio = completed / total if total > 0 else 0
        percent = ratio * 100
        logger = logging.getLogger(__name__)
        logger.info(f"下载进度: {percent:.1f}% ({completed}/{total})")


def get_default_output_dir() -> str:
    """获取默认输出目录（桌面）"""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.exists(desktop):
        return desktop
    return os.getcwd()


def download_video(
    url: str,
    output_name: str = "",
    workers: int = 20,
    proxy: str = "",
    keep: bool = False,
    stream_index: int = -1,
    custom_headers: dict = None,
    output_dir: str = "",
    logger: logging.Logger = None,
) -> str:
    """
    下载单个 m3u8 视频

    Returns:
        最终输出文件路径
    """
    custom_headers = custom_headers or {}

    # 设置默认输出目录
    if not output_dir:
        output_dir = get_default_output_dir()

    # 设置默认输出文件名
    if not output_name:
        output_name = "output.ts"

    # 如果 output_name 是相对路径，则拼接输出目录
    if not os.path.isabs(output_name):
        output_path = os.path.join(output_dir, output_name)
    else:
        output_path = output_name

    temp_dir = os.path.join(os.path.dirname(output_path), ".m3u8_temp")

    try:
        # 1. 获取并解析 m3u8
        logger.info(f"获取 m3u8: {url}")
        content = fetch_m3u8(url, custom_headers, proxy)
        base_url = get_base_url(url)
        playlist = parse_m3u8(content, base_url)

        # 2. 如果是 master playlist，选择码率流
        if playlist.is_master:
            if not playlist.streams:
                raise RuntimeError("master playlist 中没有找到码率流")

            logger.info("可用的码率流:")
            for i, stream in enumerate(playlist.streams):
                logger.info(f"  [{i}] {stream.name} ({stream.bandwidth}bps)")

            if stream_index >= 0 and stream_index < len(playlist.streams):
                selected = stream_index
            else:
                # 默认选择最高码率
                selected = max(range(len(playlist.streams)),
                             key=lambda i: playlist.streams[i].bandwidth)
                logger.info(f"自动选择最高码率: [{selected}]")

            stream_url = playlist.streams[selected].url
            logger.info(f"获取 media playlist: {stream_url}")
            content = fetch_m3u8(stream_url, custom_headers, proxy)
            base_url = get_base_url(stream_url)
            playlist = parse_m3u8(content, base_url)

        # 3. 检查分片
        if not playlist.segments:
            raise RuntimeError("没有找到 TS 分片，请检查 m3u8 地址是否正确")

        total_duration = playlist.total_duration
        logger.info(f"共 {len(playlist.segments)} 个分片，总时长 {format_duration(total_duration)}")

        has_encryption = any(s.encryption_method for s in playlist.segments)
        if has_encryption:
            logger.info("检测到 AES-128 加密，下载后将自动解密")

        # 4. 下载分片
        logger.info(f"临时目录: {temp_dir}")
        ts_files = download_all(
            playlist.segments,
            temp_dir,
            max_workers=workers,
            headers=custom_headers,
            proxy=proxy,
            progress_callback=print_progress,
        )

        if not ts_files:
            raise RuntimeError("没有成功下载任何分片")

        # 5. 解密（如果需要）
        if has_encryption:
            logger.info("正在解密分片...")
            ts_files = decrypt_files(ts_files, playlist.segments, custom_headers, proxy)
            logger.info("解密完成")

        # 6. 合并为 TS
        logger.info("正在合并分片...")
        final_path = merge_to_ts(ts_files, output_path)
        logger.info(f"下载完成: {final_path}")

        return final_path

    finally:
        # 7. 清理临时文件（无论成功或失败都清理）
        if not keep and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                logger.info("临时文件已清理")
            except Exception as e:
                logger.warning(f"清理临时文件失败: {e}")


def parse_batch_file(file_path: str) -> list:
    """
    解析批量下载文件

    文件格式：
    - 每行一个 URL
    - 可选：URL 后跟空格和输出文件名
    - 以 # 开头的行为注释

    示例：
    https://example.com/video1.m3u8 电影1.ts
    https://example.com/video2.m3u8
    # 这是注释
    """
    tasks = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split(None, 1)
            url = parts[0]
            name = parts[1] if len(parts) > 1 else ""

            if not url.startswith("http"):
                logger.warning(f"第 {line_num} 行: 无效的 URL，跳过")
                continue

            tasks.append((url, name))

    return tasks


def main():
    parser = argparse.ArgumentParser(
        description="m3u8 视频下载工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  单个下载（默认保存到桌面）:
    python main.py https://example.com/video.m3u8
    python main.py https://example.com/video.m3u8 -o movie.ts
    python main.py https://example.com/video.m3u8 -d D:/Videos -o movie.ts

  批量下载:
    python main.py -f urls.txt
    python main.py -f urls.txt -d D:/Downloads

  批量文件格式 (urls.txt):
    https://example.com/video1.m3u8 电影1.ts
    https://example.com/video2.m3u8 电影2.ts
    https://example.com/video3.m3u8
        """,
    )

    # 单个下载参数
    parser.add_argument("url", nargs="?", default="", help="m3u8 文件 URL")

    # 批量下载参数
    parser.add_argument("-f", "--file", help="批量下载文件，每行一个 URL 或 URL+文件名")

    # 通用参数
    parser.add_argument("-o", "--output", default="", help="输出文件名")
    parser.add_argument("-d", "--dir", default="", help="输出目录（默认: 桌面）")
    parser.add_argument("-w", "--workers", type=int, default=20, help="下载并发数（默认: 20）")
    parser.add_argument("-p", "--proxy", default="", help="代理地址，如 http://127.0.0.1:7890")
    parser.add_argument("-k", "--keep", action="store_true", help="保留临时 TS 分片文件")
    parser.add_argument("-s", "--stream", type=int, default=-1, help="选择码率流索引")
    parser.add_argument("-v", "--verbose", action="store_true", help="显示详细日志")
    parser.add_argument("--headers", nargs="*", default=[], help="自定义请求头，格式: Key=Value")

    args = parser.parse_args()
    setup_logging(args.verbose)
    global logger
    logger = logging.getLogger(__name__)

    # 解析自定义请求头
    custom_headers = {}
    for h in args.headers:
        if "=" in h:
            k, v = h.split("=", 1)
            custom_headers[k.strip()] = v.strip()

    # 批量下载模式
    if args.file:
        if not os.path.isfile(args.file):
            logger.error(f"批量下载文件不存在: {args.file}")
            sys.exit(1)

        tasks = parse_batch_file(args.file)
        if not tasks:
            logger.error("批量下载文件中没有有效的 URL")
            sys.exit(1)

        # 设置输出目录
        output_dir = args.dir if args.dir else get_default_output_dir()
        if not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        logger.info(f"输出目录: {output_dir}")

        logger.info(f"批量下载: 共 {len(tasks)} 个任务")

        success_count = 0
        for i, (url, name) in enumerate(tasks, 1):
            logger.info(f"\n{'='*50}")
            logger.info(f"[{i}/{len(tasks)}] 开始下载: {url}")

            # 确定输出文件名
            if name:
                output_name = name
            elif args.output:
                output_name = args.output
            else:
                output_name = f"video_{i}.ts"

            try:
                download_video(
                    url=url,
                    output_name=output_name,
                    workers=args.workers,
                    proxy=args.proxy,
                    keep=args.keep,
                    stream_index=args.stream,
                    custom_headers=custom_headers,
                    output_dir=output_dir,
                    logger=logger,
                )
                success_count += 1
            except Exception as e:
                logger.error(f"[{i}/{len(tasks)}] 下载失败: {e}")
                continue

        logger.info(f"\n{'='*50}")
        logger.info(f"批量下载完成: 成功 {success_count}/{len(tasks)}")

    # 单个下载模式
    elif args.url:
        output_name = args.output or "output.ts"
        output_dir = args.dir if args.dir else get_default_output_dir()
        logger.info(f"输出目录: {output_dir}")
        try:
            download_video(
                url=args.url,
                output_name=output_name,
                workers=args.workers,
                proxy=args.proxy,
                keep=args.keep,
                stream_index=args.stream,
                custom_headers=custom_headers,
                output_dir=output_dir,
                logger=logger,
            )
        except KeyboardInterrupt:
            print("\n下载已取消")
            sys.exit(1)
        except Exception as e:
            logger.error(f"下载失败: {e}")
            if args.verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
