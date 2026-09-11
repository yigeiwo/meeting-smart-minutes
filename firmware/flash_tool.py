# -*- coding: utf-8 -*-
import sys
import subprocess
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def find_esp_port():
    try:
        import serial.tools.list_ports
        ports = list(serial.tools.list_ports.comports())
        for p in ports:
            if "303A" in (p.hwid or "").upper():
                return p.device, p.description
        for p in ports:
            if p.device != "COM1":
                return p.device, p.description
        if ports:
            return ports[0].device, ports[0].description
    except Exception:
        pass
    return "COM3", "COM3"

def main():
    print("=" * 60)
    print("      [FoloToy AI Passport 智能胸卡固件烧录工具]")
    print("=" * 60)
    
    port, desc = find_esp_port()
    print(f"\n[1/3] 检测到硬件端口: {port} ({desc})")
    
    bin_file = Path(__file__).parent / "FoloToy-AI-Passport-full.bin"
    if not bin_file.exists():
        bin_file = Path(r"c:\Users\p\Desktop\ai 卡片\firmware\FoloToy-AI-Passport-full.bin")
    
    if not bin_file.exists():
        print(f"[ERROR] 未找到固件镜像: {bin_file}")
        sys.exit(1)
        
    print(f"[2/3] 固件文件: {bin_file.name} ({bin_file.stat().st_size} 字节)")
    print(f"[3/3] 正在启动高速烧录 (波特率 460800 / DIO 40MHz / 8MB)...")
    print("-" * 60)
    
    cmd = [
        sys.executable, "-m", "esptool",
        "--chip", "esp32c3",
        "--port", port,
        "--baud", "460800",
        "--before", "default-reset",
        "--after", "hard-reset",
        "write-flash",
        "--flash-mode", "dio",
        "--flash-freq", "40m",
        "--flash-size", "8MB",
        "0x0", str(bin_file)
    ]
    
    ret = subprocess.run(cmd)
    print("-" * 60)
    if ret.returncode == 0:
        print("\n[SUCCESS] 固件已成功完整写入 AI Passport！")
        print("提示：若卡片未自动重启，请将 Type-C 数据线拔下重新插上即可开机。")
    else:
        print("\n[ERROR] 烧录未成功完成，请检查端口是否被占用。")

if __name__ == "__main__":
    main()
