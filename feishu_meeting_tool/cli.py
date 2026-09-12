# -*- coding: utf-8 -*-
import os
import sys
import argparse
import uvicorn
import logging
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from .config import get_config, save_config
from .feishu_client import FeishuClient
from .ai_summarizer import AISummarizer
from .audio_pipeline import AudioPipeline
from .card_bridge import CardBridge

logger = logging.getLogger("cli")


def main():
    parser = argparse.ArgumentParser(
        prog="feishu-meeting",
        description="飞书会议自动妙记总结工具 (AI Passport 硬件联动版)",
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # 1. Web
    web_parser = subparsers.add_parser("web", help="启动 Web 工作台与硬件桥接服务")
    web_parser.add_argument("--host", default="0.0.0.0", help="Web 监听地址 (0.0.0.0 允许局域网访问)")
    web_parser.add_argument("--port", type=int, default=8000, help="Web 端口")
    web_parser.add_argument("--bridge-port", type=int, default=5566, help="AI Passport 桥接端口")

    # 2. Summarize
    sum_parser = subparsers.add_parser("summarize", help="总结本地音频或文本文件")
    sum_parser.add_argument("file", help="音频文件 (.wav/.mp3)或转录文本文件 (.txt)")
    sum_parser.add_argument("-s", "--scenario", default="general", choices=["general", "tech", "prd", "business", "brainstorm"])
    sum_parser.add_argument("-o", "--output", help="输出 Markdown 文件路径")
    sum_parser.add_argument("--push-bot", action="store_true", help="一键推送到飞书群机器人")
    sum_parser.add_argument("--create-doc", action="store_true", help="自动创建飞书云文档")

    # 3. Minutes
    min_parser = subparsers.add_parser("minutes", help="直连并总结飞书妙记 (Minutes)")
    min_parser.add_argument("url_or_token", help="飞书妙记分享链接或 minute_token")
    min_parser.add_argument("-s", "--scenario", default="general")
    min_parser.add_argument("--push-bot", action="store_true")
    min_parser.add_argument("--create-doc", action="store_true")

    # 4. Bridge
    bridge_parser = subparsers.add_parser("bridge", help="仅启动 AI Passport TCP 硬件桥接服务")
    bridge_parser.add_argument("--port", type=int, default=5566)

    # 5. Test Feishu
    test_parser = subparsers.add_parser("test-feishu", help="测试飞书开放平台连接")

    args = parser.parse_args()

    if not args.command or args.command == "web":
        host = getattr(args, "host", "0.0.0.0")
        port = getattr(args, "port", 8000)
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            local_ip = "127.0.0.1"

        print("=======================================================")
        print("[INFO] 飞书会议智能妙记总结系统正在启动...")
        print(f"[URL]  本地控制台: http://127.0.0.1:{port}")
        if local_ip != "127.0.0.1":
            print(f"[URL]  局域网控制台: http://{local_ip}:{port}")
        print(f"[PORT] AI Passport 硬件桥接端口: 5566 (TCP)")
        print("=======================================================")
        uvicorn.run("feishu_meeting_tool.web_server:app", host=host, port=port, reload=False)

    elif args.command == "summarize":
        cfg = get_config()
        pipeline = AudioPipeline(records_dir=cfg.records_dir,
                                 api_key=cfg.asr_effective_api_key,
                                 base_url=cfg.asr_effective_base_url,
                                 model=cfg.asr_effective_model)
        summarizer = AISummarizer(api_key=cfg.llm_api_key, base_url=cfg.llm_base_url, model=cfg.llm_model)

        file_path = Path(args.file)
        if not file_path.exists():
            print(f"文件不存在: {file_path}")
            sys.exit(1)

        print(f"[*] 正在处理文件: {file_path.name}...")
        if file_path.suffix.lower() in [".txt", ".md"]:
            with open(file_path, "r", encoding="utf-8") as f:
                transcript = f.read()
        else:
            transcript = pipeline.transcribe(str(file_path))

        print(f"[*] 正在调用 AI 进行 [{args.scenario}] 场景提炼...")
        summary = summarizer.summarize(transcript, scenario=args.scenario)
        md_text = summarizer.format_to_markdown(summary)

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(md_text)
            print(f"[+] 纪要已保存至: {args.output}")
        else:
            print("\n" + "=" * 50)
            print(md_text)
            print("=" * 50)

        feishu = FeishuClient(
            app_id=cfg.feishu_app_id,
            app_secret=cfg.feishu_app_secret,
            webhook_url=cfg.feishu_bot_webhook_url,
            doc_folder_token=cfg.feishu_doc_folder_token,
        )

        if args.push_bot:
            res = feishu.send_meeting_summary_card(summary)
            print(f"[+] 飞书机器人推送: {res}")

        if args.create_doc:
            doc_url = feishu.create_feishu_doc(summary)
            print(f"[+] 飞书云文档已创建: {doc_url}")

    elif args.command == "minutes":
        cfg = get_config()
        feishu = FeishuClient(
            app_id=cfg.feishu_app_id,
            app_secret=cfg.feishu_app_secret,
            webhook_url=cfg.feishu_bot_webhook_url,
        )
        token = feishu.extract_minute_token(args.url_or_token)
        print(f"[*] 正在获取飞书妙记: {token}...")
        try:
            info = feishu.get_minute_info(token)
            print(f"[+] 获取成功: {info.get('title', '无标题')}")
        except Exception as e:
            print(f"[!] 获取妙记失败: {e}")

    elif args.command == "bridge":
        bridge = CardBridge(port=args.port)
        bridge.start()
        print(f"[*] AI Passport 硬件桥接服务已在 0.0.0.0:{args.port} 启动，按 Ctrl+C 退出...")
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            bridge.stop()

    elif args.command == "test-feishu":
        cfg = get_config()
        feishu = FeishuClient(app_id=cfg.feishu_app_id, app_secret=cfg.feishu_app_secret)
        try:
            tok = feishu.get_tenant_access_token(force_refresh=True)
            print(f"[✔] 飞书 API 连接成功！Tenant Token: {tok[:12]}...")
        except Exception as e:
            print(f"[✘] 飞书 API 连接失败: {e}")


if __name__ == "__main__":
    main()
