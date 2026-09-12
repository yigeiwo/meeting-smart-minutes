# -*- coding: utf-8 -*-
import os
import wave
import struct
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from openai import OpenAI

logger = logging.getLogger("audio_pipeline")


class AudioPipeline:
    def __init__(
        self,
        records_dir: str = "records",
        api_key: str = "",
        base_url: str = "",
        model: str = "",
    ):
        self.records_dir = Path(records_dir)
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key.strip()
        self.base_url = base_url.strip()
        # 转写模型名必须可配: 各家 OpenAI 兼容网关的命名不同
        # (OpenAI 用 whisper-1, grok 网关用 grok-stt), 写死会导致 404 模型不存在。
        self.model = (model or "whisper-1").strip()

    def save_pcm_as_wav(
        self,
        pcm_bytes: bytes,
        sample_rate: int = 16000,
        channels: int = 1,
        bits_per_sample: int = 16,
        output_filename: Optional[str] = None,
    ) -> Path:
        if not output_filename:
            import time
            output_filename = f"card_record_{time.strftime('%Y%m%d_%H%M%S')}.wav"

        out_path = self.records_dir / output_filename
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(bits_per_sample // 8)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)

        logger.info(f"已成功将 PCM 音频保存为 WAV: {out_path} (大小: {len(pcm_bytes)} 字节)")
        return out_path

    def transcribe(self, audio_file_path: str) -> str:
        path = Path(audio_file_path)
        if not path.exists() or path.stat().st_size == 0:
            raise FileNotFoundError(f"音频文件不存在或大小为 0 字节: {audio_file_path}")

        # 1. 尝试使用配置的云端/网关 Whisper 兼容 API
        if self.api_key:
            try:
                client = OpenAI(api_key=self.api_key, base_url=self.base_url or None, timeout=60.0)
                with open(path, "rb") as f:
                    res = client.audio.transcriptions.create(
                        model=self.model,
                        file=f,
                    )
                transcript_text = getattr(res, "text", str(res)).strip()
                if transcript_text:
                    logger.info(
                        f"✔ 真实语音识别成功 ({self.model} @ {self.base_url or 'OpenAI 默认'}): "
                        f"{len(transcript_text)} 字")
                    return transcript_text
                logger.warning(f"转写接口返回空文本 (模型={self.model}), 可能是音频里没有人声")
            except Exception as e:
                # 把模型名与网关一起打出来, 否则"模型不存在/密钥无效"这类配置错误很难定位
                logger.warning(f"转写失败 (模型={self.model} 网关={self.base_url or '默认'}): {e}")

        # 2. 尝试本地 faster-whisper 引擎
        try:
            from faster_whisper import WhisperModel
            logger.info("正在调用本地 Faster-Whisper 模型进行离线语音识别...")
            model = WhisperModel("tiny", device="cpu", compute_type="int8")
            segments, info = model.transcribe(str(path), language="zh")
            full_text = "".join([seg.text for seg in segments]).strip()
            if full_text:
                logger.info(f"✔ 本地 Faster-Whisper 识别成功: {len(full_text)} 字")
                return full_text
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"本地 Faster-Whisper 识别异常: {e}")

        # 3. 若无可用 ASR 引擎，抛出明确错误提示
        raise RuntimeError(
            "语音转文字失败：未配置有效的语音识别 (ASR) 密钥。\n"
            "请前往【多 AI 模型与系统配置】填写支持音频识别的 API Key（如 OpenAI / New API），或直接在文本框粘贴会议讨论内容！"
        )
